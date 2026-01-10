# -*- coding: utf-8 -*-
"""
네스팅 엔진 모듈 (NFP 개선 버전)
- NFP(No-Fit Polygon) 기반 2D 패턴 네스팅
- Python 3 + Shapely + pyclipper
- NFP 경로 기반 최적 배치 알고리즘
"""

import pyclipper
from shapely.geometry import Polygon as ShapelyPolygon, Point, box, LineString
from shapely.prepared import prep
from shapely import affinity
from shapely.strtree import STRtree
from shapely.ops import unary_union
import copy
import math

# pyclipper 스케일 (정수 변환용)
CLIPPER_SCALE = 1000


class NestingEngine:
    """NFP 기반 네스팅 엔진 (NFP 개선 버전)"""

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
        self._placed_coords = []    # 원본 좌표들
        self._spatial_index = None  # STRtree 공간 인덱스

        # NFP 사용 여부 (현재 Grid 방식이 더 효율적)
        self.use_nfp = False

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
        네스팅 실행 (NFP 개선 버전)

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
        self._placed_coords = []  # NFP 계산용 좌표 저장
        self._spatial_index = None
        self.nfp_cache = {}  # NFP 캐시 초기화

        for pattern in self.patterns:
            best_position = None
            best_y = float('inf')
            best_rotation = 0
            best_coords = None

            # 각 회전 각도에 대해 최적 위치 찾기
            for rotation in rotations:
                rotated_coords = self._rotate_polygon(pattern['coords'], rotation)

                # 간격은 충돌 체크에서 적용 (패턴 자체는 원본 사용)
                # 가능한 배치 위치 찾기
                if self.use_nfp:
                    position = self._find_position_nfp(rotated_coords)
                else:
                    position = self._find_position_optimized(rotated_coords)

                if position and position[1] < best_y:
                    best_position = position
                    best_y = position[1]
                    best_rotation = rotation
                    best_coords = rotated_coords

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
                self._placed_coords.append(final_coords)  # NFP 계산용

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
        """최적화된 Bottom-Left 알고리즘 (적응형 스텝 + 우선 탐색)"""
        pattern_bounds = self._get_bounds(pattern_coords)
        pattern_width = pattern_bounds['width']
        pattern_height = pattern_bounds['height']

        # 배치된 패턴이 없으면 원점에 배치
        if not self._placed_polygons:
            return (0, 0)

        # 현재까지 사용된 최대 Y 위치
        max_used_y = 0
        for bounds in self._placed_bounds:
            max_used_y = max(max_used_y, bounds[3])

        # 1단계: 우선 탐색 위치 (인접 영역)
        priority_positions = []
        for placed_bounds in self._placed_bounds:
            # 패턴 오른쪽
            right_x = placed_bounds[2] + self.spacing
            if right_x + pattern_width <= self.sheet_width:
                priority_positions.append((right_x, placed_bounds[1]))
            # 패턴 위
            top_y = placed_bounds[3] + self.spacing
            priority_positions.append((0, top_y))
            priority_positions.append((placed_bounds[0], top_y))

        # 우선 위치 Y 기준 정렬
        priority_positions.sort(key=lambda p: (p[1], p[0]))

        # 우선 위치에서 빠르게 검색
        for x, y in priority_positions:
            if x < 0 or y < 0:
                continue
            test_coords = self._translate_polygon(pattern_coords, x, y)
            if self._is_inside_sheet(test_coords) and not self._check_collision_optimized(test_coords):
                return (x, y)

        # 2단계: 그리드 탐색 (5mm 스텝 - 높은 효율)
        step = 5
        search_height = max_used_y + pattern_height + self.spacing * 2

        y = 0
        while y <= search_height:
            x = 0
            while x <= self.sheet_width - pattern_width:
                test_coords = self._translate_polygon(pattern_coords, x, y)

                if not self._is_inside_sheet(test_coords):
                    x += step
                    continue

                if not self._check_collision_optimized(test_coords):
                    return (x, y)

                x += step
            y += step

        # 찾지 못했으면 새 행에 배치
        new_y = int(max_used_y + self.spacing)
        for x in range(0, int(self.sheet_width - pattern_width) + 1, step):
            test_coords = self._translate_polygon(pattern_coords, x, new_y)
            if self._is_inside_sheet(test_coords) and not self._check_collision_optimized(test_coords):
                return (x, new_y)

        return (0, new_y + int(pattern_height) + self.spacing)

    def _check_collision_optimized(self, coords):
        """최적화된 충돌 체크 (바운딩박스 + 직접비교)"""
        try:
            test_poly = ShapelyPolygon(coords)
            if not test_poly.is_valid:
                test_poly = test_poly.buffer(0)

            test_bounds = test_poly.bounds  # (minx, miny, maxx, maxy)

            # 모든 배치된 폴리곤과 직접 비교 (안정성 우선)
            for i, placed_poly in enumerate(self._placed_polygons):
                placed_bounds = self._placed_bounds[i]

                # 바운딩박스 사전 체크 (빠른 배제) - 간격 포함
                margin = self.spacing
                if (test_bounds[2] + margin < placed_bounds[0] or
                    test_bounds[0] - margin > placed_bounds[2] or
                    test_bounds[3] + margin < placed_bounds[1] or
                    test_bounds[1] - margin > placed_bounds[3]):
                    continue  # 바운딩박스 겹치지 않음

                # 정밀 충돌 체크 (교차 또는 접촉)
                if test_poly.intersects(placed_poly):
                    return True

                # 거리 기반 추가 체크 (간격 확보)
                if test_poly.distance(placed_poly) < self.spacing:  # 간격 이내면 충돌로 간주
                    return True

            return False
        except Exception as e:
            return True  # 에러시 충돌로 처리

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

    # ==================== NFP 관련 함수 ====================

    def _to_clipper_coords(self, coords):
        """좌표를 pyclipper 정수 형식으로 변환"""
        return [[int(p[0] * CLIPPER_SCALE), int(p[1] * CLIPPER_SCALE)] for p in coords]

    def _from_clipper_coords(self, coords):
        """pyclipper 정수 형식을 실수 좌표로 변환"""
        return [[p[0] / CLIPPER_SCALE, p[1] / CLIPPER_SCALE] for p in coords]

    def _calculate_nfp(self, fixed_coords, moving_coords):
        """
        NFP(No-Fit Polygon) 계산
        - fixed_coords: 고정된 패턴 좌표
        - moving_coords: 이동할 패턴 좌표
        - 반환: NFP 좌표 리스트 (moving 패턴의 기준점이 위치할 수 없는 영역)
        """
        cache_key = (tuple(map(tuple, fixed_coords)), tuple(map(tuple, moving_coords)))
        if cache_key in self.nfp_cache:
            return self.nfp_cache[cache_key]

        try:
            # moving 폴리곤을 원점 기준으로 반전 (Minkowski difference)
            moving_reflected = [[-p[0], -p[1]] for p in moving_coords]

            # pyclipper 형식으로 변환
            fixed_scaled = self._to_clipper_coords(fixed_coords)
            moving_scaled = self._to_clipper_coords(moving_reflected)

            # Minkowski Sum 계산 = NFP
            result = pyclipper.MinkowskiSum(fixed_scaled, moving_scaled, True)

            if result and len(result) > 0:
                # 가장 큰 폴리곤 선택 (외곽)
                largest = max(result, key=lambda p: abs(pyclipper.Area(p)))
                nfp = self._from_clipper_coords(largest)
                self.nfp_cache[cache_key] = nfp
                return nfp

        except Exception as e:
            pass

        return None

    def _calculate_ifp(self, pattern_coords):
        """
        IFP(Inner Fit Polygon) 계산 - 시트 내부에서 패턴이 배치될 수 있는 영역
        - pattern_coords: 패턴 좌표 (원점 기준 정규화된)
        - 반환: IFP 좌표 리스트
        """
        bounds = self._get_bounds(pattern_coords)
        pattern_width = bounds['width']
        pattern_height = bounds['height']

        # 시트에서 패턴 크기만큼 축소한 영역 = 패턴 기준점이 위치할 수 있는 영역
        ifp = [
            [0, 0],
            [self.sheet_width - pattern_width, 0],
            [self.sheet_width - pattern_width, self.sheet_height - pattern_height],
            [0, self.sheet_height - pattern_height]
        ]
        return ifp

    def _get_nfp_positions(self, pattern_coords):
        """
        NFP를 이용해 가능한 배치 위치 후보 생성
        - 모든 배치된 패턴과의 NFP 경계점 + IFP 경계점 반환
        """
        positions = []
        bounds = self._get_bounds(pattern_coords)

        # IFP 꼭짓점 (시트 경계)
        ifp = self._calculate_ifp(pattern_coords)
        for point in ifp:
            positions.append((point[0], point[1], 'ifp'))

        # 배치된 각 패턴과의 NFP 경계점
        for i, placed_coords in enumerate(self._placed_coords):
            nfp = self._calculate_nfp(placed_coords, pattern_coords)
            if nfp:
                # NFP 꼭짓점들을 후보에 추가
                for point in nfp:
                    # 시트 범위 내인지 확인
                    if (0 <= point[0] <= self.sheet_width - bounds['width'] and
                        0 <= point[1] <= self.sheet_height - bounds['height']):
                        positions.append((point[0], point[1], f'nfp_{i}'))

                # NFP 변의 중점도 추가 (더 정밀한 배치)
                for j in range(len(nfp)):
                    p1 = nfp[j]
                    p2 = nfp[(j + 1) % len(nfp)]
                    mid = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2]
                    if (0 <= mid[0] <= self.sheet_width - bounds['width'] and
                        0 <= mid[1] <= self.sheet_height - bounds['height']):
                        positions.append((mid[0], mid[1], f'nfp_mid_{i}'))

        return positions

    def _find_position_nfp(self, pattern_coords):
        """NFP 기반 최적 배치 위치 찾기 - 빈틈 채우기 방식"""
        bounds = self._get_bounds(pattern_coords)
        pattern_width = bounds['width']
        pattern_height = bounds['height']

        # 배치된 패턴이 없으면 원점에 배치
        if not self._placed_coords:
            return (0, 0)

        # 후보 위치 수집 (NFP 경계 + 인접 위치)
        candidates = []
        max_y = max([b[3] for b in self._placed_bounds]) if self._placed_bounds else 0

        # 1. NFP 경계점만 수집 (그리드 제외 - 속도 향상)
        for i, placed_coords in enumerate(self._placed_coords):
            nfp = self._calculate_nfp(placed_coords, pattern_coords)
            if nfp:
                for point in nfp:
                    if (0 <= point[0] <= self.sheet_width - pattern_width and
                        0 <= point[1] <= self.sheet_height - pattern_height):
                        candidates.append((point[0], point[1]))

        # 2. 인접 배치 위치 추가 (빈틈 채우기)
        for placed_bounds in self._placed_bounds:
            # 패턴 오른쪽 (간격 포함)
            right_x = placed_bounds[2] + self.spacing
            if right_x + pattern_width <= self.sheet_width:
                for y_offset in [0, -pattern_height/4, -pattern_height/2]:
                    candidates.append((right_x, max(0, placed_bounds[1] + y_offset)))

            # 패턴 위쪽
            top_y = placed_bounds[3] + self.spacing
            for x_offset in [0, pattern_width/4, pattern_width/2]:
                candidates.append((max(0, placed_bounds[0] + x_offset), top_y))

        # 3. 시트 왼쪽 경계
        for y in range(0, int(max_y + pattern_height), 50):
            candidates.append((0, y))

        # 4. 새 행 시작점
        candidates.append((0, max_y + self.spacing))

        # 5. 세밀한 그리드 (제한된 영역만)
        step = 5  # 5mm 정밀도
        for y in range(0, min(int(max_y + 100), 500), step):
            for x in range(0, int(self.sheet_width - pattern_width), step * 2):
                candidates.append((x, y))

        # 중복 제거 및 정렬 (Y 우선, X 차순)
        candidates = list(set(candidates))
        candidates.sort(key=lambda p: (p[1], p[0]))

        # 최적 위치 찾기
        for x, y in candidates:
            test_coords = self._translate_polygon(pattern_coords, x, y)

            if not self._is_inside_sheet(test_coords):
                continue

            if not self._check_collision_optimized(test_coords):
                return (x, y)

        # 못 찾으면 새 행에 배치
        return (0, int(max_y + self.spacing))

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
