# -*- coding: utf-8 -*-
"""
네스팅 엔진 모듈 (최적화 버전)
- NFP(No-Fit Polygon) 기반 2D 패턴 네스팅
- Python 3 + Shapely + pyclipper
- 최적화: 공간 인덱싱, 바운딩박스 사전체크, 적응형 그리드
"""

import pyclipper
from shapely.geometry import Polygon as ShapelyPolygon, Point, box
from shapely.prepared import prep
from shapely import affinity
from shapely.strtree import STRtree
import copy
import math


class NestingEngine:
    """NFP 기반 네스팅 엔진 (최적화 버전)"""

    def __init__(self, sheet_width, sheet_height=10000, spacing=5):
        """
        Args:
            sheet_width: 원단 폭 (mm)
            sheet_height: 원단 길이 (mm), 기본값 10000 (무한대 가정)
            spacing: 패턴 간 간격 (mm)
        """
        self.sheet_width = sheet_width
        self.sheet_height = sheet_height
        self.spacing = spacing
        self.patterns = []  # 배치할 패턴들
        self.placements = []  # 배치 결과
        self.nfp_cache = {}  # NFP 캐시

        # 최적화용 캐시
        self._placed_polygons = []  # Shapely Polygon 객체들
        self._placed_bounds = []    # 바운딩 박스들
        self._spatial_index = None  # STRtree 공간 인덱스

    def add_pattern(self, polygon, quantity=1, pattern_id=None):
        """
        패턴 추가

        Args:
            polygon: Shapely Polygon 또는 좌표 리스트
            quantity: 수량
            pattern_id: 패턴 ID (없으면 자동 생성)
        """
        if isinstance(polygon, ShapelyPolygon):
            coords = list(polygon.exterior.coords)[:-1]  # 마지막 중복 점 제거
        else:
            coords = polygon

        # mm 단위로 변환 (cm → mm)
        coords_mm = [[p[0] * 10, p[1] * 10] for p in coords]

        for i in range(quantity):
            pid = f"{pattern_id}_{i}" if pattern_id else f"pattern_{len(self.patterns)}"
            self.patterns.append({
                'id': pid,
                'coords': coords_mm,
                'area': abs(self._polygon_area(coords_mm)),
                'placed': False,
                'x': 0,
                'y': 0,
                'rotation': 0
            })

    def add_patterns_from_dataframe(self, df, polygons):
        """
        요척 산출서 데이터프레임에서 패턴 추가

        Args:
            df: 상세 리스트 데이터프레임
            polygons: Shapely Polygon 리스트
        """
        for i, (poly, row) in enumerate(zip(polygons, df.itertuples())):
            quantity = int(row.수량) if hasattr(row, '수량') else 1
            pattern_id = f"{row.구분}_{i}" if hasattr(row, '구분') else f"pattern_{i}"
            self.add_pattern(poly, quantity=quantity, pattern_id=pattern_id)

    def run(self, rotations=[0], iterations=3):
        """
        네스팅 실행 (최적화 버전)

        Args:
            rotations: 허용 회전 각도 리스트 (예: [0, 90, 180, 270])
            iterations: 최적화 반복 횟수

        Returns:
            dict: 네스팅 결과
        """
        # 면적 기준 내림차순 정렬 (큰 패턴 먼저 배치)
        self.patterns.sort(key=lambda p: p['area'], reverse=True)

        # 배치 초기화
        self.placements = []
        self._placed_polygons = []
        self._placed_bounds = []
        self._spatial_index = None

        # 현재 행의 Y 위치와 높이 추적 (행 기반 패킹)
        current_row_y = 0
        current_row_height = 0
        current_row_x = 0

        for pattern in self.patterns:
            best_position = None
            best_y = float('inf')
            best_rotation = 0
            best_coords = None

            # 각 회전 각도에 대해 최적 위치 찾기
            for rotation in rotations:
                rotated_coords = self._rotate_polygon(pattern['coords'], rotation)

                # 간격 적용
                offset_coords = self._offset_polygon(rotated_coords, self.spacing)
                if offset_coords is None:
                    continue

                # 가능한 배치 위치 찾기 (최적화된 버전)
                position = self._find_position_optimized(offset_coords)

                if position and position[1] < best_y:
                    best_position = position
                    best_y = position[1]
                    best_rotation = rotation
                    best_coords = offset_coords

            if best_position:
                # 배치 적용
                pattern['x'] = best_position[0]
                pattern['y'] = best_position[1]
                pattern['rotation'] = best_rotation
                pattern['placed'] = True

                # 배치된 폴리곤 추가
                final_coords = self._rotate_polygon(pattern['coords'], best_rotation)
                final_coords = self._translate_polygon(final_coords, best_position[0], best_position[1])

                # Shapely 폴리곤 생성 및 캐시
                placed_poly = ShapelyPolygon(final_coords)
                if not placed_poly.is_valid:
                    placed_poly = placed_poly.buffer(0)
                self._placed_polygons.append(placed_poly)
                self._placed_bounds.append(placed_poly.bounds)

                # 공간 인덱스 재구축 (5개마다)
                if len(self._placed_polygons) % 5 == 0:
                    self._spatial_index = STRtree(self._placed_polygons)

                self.placements.append({
                    'id': pattern['id'],
                    'x': best_position[0],
                    'y': best_position[1],
                    'rotation': best_rotation,
                    'coords': final_coords
                })

        return self._calculate_result()

    def _find_position_optimized(self, pattern_coords):
        """최적화된 Bottom-Left 알고리즘으로 배치 위치 찾기"""
        pattern_bounds = self._get_bounds(pattern_coords)
        pattern_width = pattern_bounds['width']
        pattern_height = pattern_bounds['height']

        # 적응형 스텝 크기 (패턴 크기에 비례)
        step_x = max(20, int(pattern_width / 10))
        step_y = max(20, int(pattern_height / 10))

        # 배치된 패턴이 없으면 원점에 배치
        if not self._placed_polygons:
            return (0, 0)

        # 현재까지 사용된 최대 Y 위치 + 여유 공간
        max_used_y = 0
        for bounds in self._placed_bounds:
            max_used_y = max(max_used_y, bounds[3])  # bounds[3] = max_y

        # 검색 범위 제한 (성능 최적화)
        search_height = min(max_used_y + pattern_height * 2, self.sheet_height)

        best_position = None
        best_y = float('inf')

        # Y 우선 탐색 (Bottom-Left)
        y = 0
        while y < search_height:
            x = 0
            while x < self.sheet_width - pattern_width:
                # 패턴을 해당 위치로 이동
                test_coords = self._translate_polygon(pattern_coords, x, y)

                # 시트 범위 체크
                if not self._is_inside_sheet(test_coords):
                    x += step_x
                    continue

                # 최적화된 충돌 체크
                if not self._check_collision_optimized(test_coords):
                    if y < best_y:
                        best_y = y
                        best_position = (x, y)
                    break  # 현재 Y에서 첫 위치 찾으면 종료

                x += step_x

            if best_position and best_position[1] == y:
                break  # 유효 위치 찾으면 종료

            y += step_y

        # 찾지 못했으면 새 행에 배치
        if best_position is None:
            best_position = (0, int(max_used_y + self.spacing))

        return best_position

    def _check_collision_optimized(self, coords):
        """최적화된 충돌 체크 (바운딩박스 + 공간인덱싱)"""
        try:
            test_poly = ShapelyPolygon(coords)
            if not test_poly.is_valid:
                test_poly = test_poly.buffer(0)

            test_bounds = test_poly.bounds  # (minx, miny, maxx, maxy)

            # 배치된 폴리곤이 적으면 직접 비교
            if len(self._placed_polygons) < 5:
                for i, placed_poly in enumerate(self._placed_polygons):
                    placed_bounds = self._placed_bounds[i]

                    # 바운딩박스 사전 체크 (빠른 배제)
                    if (test_bounds[2] < placed_bounds[0] or  # test_max_x < placed_min_x
                        test_bounds[0] > placed_bounds[2] or  # test_min_x > placed_max_x
                        test_bounds[3] < placed_bounds[1] or  # test_max_y < placed_min_y
                        test_bounds[1] > placed_bounds[3]):   # test_min_y > placed_max_y
                        continue  # 바운딩박스 겹치지 않음

                    # 정밀 충돌 체크
                    if test_poly.intersects(placed_poly):
                        return True
            else:
                # 공간 인덱싱 사용
                if self._spatial_index is None:
                    self._spatial_index = STRtree(self._placed_polygons)

                # 바운딩박스로 후보 필터링
                test_box = box(*test_bounds)
                candidates = self._spatial_index.query(test_box)

                for candidate in candidates:
                    if test_poly.intersects(candidate):
                        return True

            return False
        except Exception:
            return True

    def _find_position(self, pattern_coords, placed_polygons, sheet):
        """Bottom-Left 알고리즘으로 배치 위치 찾기 (레거시 호환)"""
        return self._find_position_optimized(pattern_coords)

    def _is_inside_sheet(self, coords):
        """패턴이 시트 안에 있는지 확인"""
        for x, y in coords:
            if x < 0 or x > self.sheet_width or y < 0 or y > self.sheet_height:
                return False
        return True

    def _check_collision(self, coords1, placed_polygons):
        """다른 패턴과 충돌하는지 확인"""
        try:
            poly1 = ShapelyPolygon(coords1)
            if not poly1.is_valid:
                poly1 = poly1.buffer(0)

            for coords2 in placed_polygons:
                poly2 = ShapelyPolygon(coords2)
                if not poly2.is_valid:
                    poly2 = poly2.buffer(0)

                if poly1.intersects(poly2):
                    return True
            return False
        except:
            return True

    def _calculate_result(self):
        """네스팅 결과 계산"""
        if not self.placements:
            return {
                'success': False,
                'message': 'No patterns placed',
                'used_length': 0,
                'efficiency': 0
            }

        # 사용된 길이 계산 (가장 높은 Y + 패턴 높이)
        max_y = 0
        total_area = 0

        for placement in self.placements:
            bounds = self._get_bounds(placement['coords'])
            max_y = max(max_y, bounds['max_y'])
            # 원본 패턴 면적 계산 (간격 제외)
            total_area += abs(self._polygon_area(placement['coords']))

        used_length = max_y + self.spacing  # mm
        sheet_area = self.sheet_width * used_length

        # 효율 계산 (최대 99.9%로 제한)
        efficiency = (total_area / sheet_area * 100) if sheet_area > 0 else 0
        efficiency = min(efficiency, 99.9)

        return {
            'success': True,
            'placed_count': len(self.placements),
            'total_count': len(self.patterns),
            'used_length_mm': used_length,
            'used_length_cm': used_length / 10,
            'used_length_m': used_length / 1000,
            'used_length_yd': used_length / 1000 * 1.09361,
            'efficiency': round(efficiency, 1),
            'placements': self.placements
        }

    def get_visualization_data(self):
        """시각화용 데이터 반환"""
        return {
            'sheet_width': self.sheet_width,
            'sheet_height': self.sheet_height,
            'placements': self.placements
        }

    # ==================== 헬퍼 함수 ====================

    def _polygon_area(self, coords):
        """다각형 면적 계산 (Shoelace formula)"""
        n = len(coords)
        area = 0
        for i in range(n):
            j = (i + 1) % n
            area += coords[i][0] * coords[j][1]
            area -= coords[j][0] * coords[i][1]
        return area / 2

    def _get_bounds(self, coords):
        """다각형 경계 박스"""
        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        return {
            'min_x': min(xs),
            'max_x': max(xs),
            'min_y': min(ys),
            'max_y': max(ys),
            'width': max(xs) - min(xs),
            'height': max(ys) - min(ys)
        }

    def _rotate_polygon(self, coords, angle):
        """다각형 회전 (원점 기준)"""
        if angle == 0:
            return coords

        # 중심점 계산
        cx = sum(p[0] for p in coords) / len(coords)
        cy = sum(p[1] for p in coords) / len(coords)

        rad = math.radians(angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        rotated = []
        for x, y in coords:
            # 중심 기준 회전
            rx = (x - cx) * cos_a - (y - cy) * sin_a + cx
            ry = (x - cx) * sin_a + (y - cy) * cos_a + cy
            rotated.append([rx, ry])

        # 원점으로 이동 (min을 0으로)
        bounds = self._get_bounds(rotated)
        return [[p[0] - bounds['min_x'], p[1] - bounds['min_y']] for p in rotated]

    def _translate_polygon(self, coords, dx, dy):
        """다각형 이동"""
        return [[p[0] + dx, p[1] + dy] for p in coords]

    def _offset_polygon(self, coords, offset):
        """다각형 오프셋 (간격 적용)"""
        try:
            co = pyclipper.PyclipperOffset()
            # 정수로 변환 (pyclipper는 정수만 처리)
            int_coords = [[int(p[0] * 100), int(p[1] * 100)] for p in coords]
            co.AddPath(int_coords, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
            result = co.Execute(int(offset * 100))

            if result and len(result) > 0:
                return [[p[0] / 100, p[1] / 100] for p in result[0]]
            return coords
        except:
            return coords


def create_nesting_visualization(result, sheet_width_cm):
    """
    네스팅 결과 시각화 (Matplotlib)

    Args:
        result: NestingEngine.run() 결과
        sheet_width_cm: 원단 폭 (cm)

    Returns:
        matplotlib Figure
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.collections import PatchCollection

    if not result['success']:
        return None

    # Figure 생성
    used_length = result['used_length_mm']
    aspect = used_length / (sheet_width_cm * 10)

    fig_width = 12
    fig_height = max(4, min(20, fig_width * aspect * 0.5))

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    # 시트 그리기
    sheet = patches.Rectangle(
        (0, 0), sheet_width_cm * 10, used_length,
        linewidth=2, edgecolor='green', facecolor='lightgreen', alpha=0.3
    )
    ax.add_patch(sheet)

    # 패턴 색상
    colors = ['#4c78a8', '#e45756', '#f58518', '#72b7b2', '#54a24b',
              '#eeca3b', '#b279a2', '#ff9da6', '#9d755d', '#bab0ac']

    # 패턴들 그리기
    for i, placement in enumerate(result['placements']):
        coords = placement['coords']
        color = colors[i % len(colors)]

        poly = patches.Polygon(
            coords, closed=True,
            facecolor=color, edgecolor='black',
            linewidth=0.5, alpha=0.7
        )
        ax.add_patch(poly)

        # 패턴 ID 표시
        cx = sum(p[0] for p in coords) / len(coords)
        cy = sum(p[1] for p in coords) / len(coords)
        ax.text(cx, cy, placement['id'].split('_')[0][:6],
                ha='center', va='center', fontsize=6, color='white', weight='bold')

    # 축 설정
    ax.set_xlim(-50, sheet_width_cm * 10 + 50)
    ax.set_ylim(-50, used_length + 50)
    ax.set_aspect('equal')
    ax.set_xlabel('Width (mm)')
    ax.set_ylabel('Length (mm)')
    ax.set_title(f"Nesting Result - Efficiency: {result['efficiency']}% | Length: {result['used_length_cm']:.1f}cm ({result['used_length_yd']:.2f}yd)")

    plt.tight_layout()
    return fig


# 테스트 코드
if __name__ == '__main__':
    print("=== NestingEngine Test ===\n")

    # 엔진 생성 (원단 폭 150cm = 1500mm)
    engine = NestingEngine(sheet_width=1500, spacing=5)

    # 테스트 패턴 추가 (cm 단위)
    # 큰 사각형 40x30cm
    engine.add_pattern([[0, 0], [40, 0], [40, 30], [0, 30]], quantity=2, pattern_id="BODY")

    # 중간 사각형 25x20cm
    engine.add_pattern([[0, 0], [25, 0], [25, 20], [0, 20]], quantity=4, pattern_id="SLEEVE")

    # 작은 사각형 15x10cm
    engine.add_pattern([[0, 0], [15, 0], [15, 10], [0, 10]], quantity=4, pattern_id="POCKET")

    # 네스팅 실행
    result = engine.run(rotations=[0, 180])

    print(f"Placed: {result['placed_count']}/{result['total_count']}")
    print(f"Used Length: {result['used_length_cm']:.1f} cm ({result['used_length_yd']:.2f} yd)")
    print(f"Efficiency: {result['efficiency']}%")

    # 시각화
    fig = create_nesting_visualization(result, 150)
    if fig:
        fig.savefig('nesting_result.png', dpi=150)
        print("\nVisualization saved: nesting_result.png")
