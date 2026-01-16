"""
프로젝트명: 스마트 의류 요척 산출서 (Smart Fabric Yield Calculator)
버전: V36 (Final Clean)
설명: DXF 패턴 파일을 분석하여 원단 소요량(요척)을 자동 산출하는 웹 애플리케이션
주요기능:
  - DXF 파일 파싱 및 형상 분석 (ezdxf, shapely)
  - 패턴 썸네일 그리드 및 인터랙티브 뷰어 (matplotlib, plotly)
  - 원단명/수량 일괄 수정 및 개별 요척 계산
  - Streamlit 기반의 반응형 UI
"""

import streamlit as st
import ezdxf
from shapely.geometry import LineString, Polygon, Point
from shapely import affinity
from shapely.ops import polygonize
import math
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import pandas as pd
import tempfile
import os
import io
import base64

# 네스팅 엔진 임포트
from nesting_engine import NestingEngine, create_nesting_visualization

# 형상 시그니처 DB (수량 추천/학습)
try:
    from pattern_db import PatternDB
    PATTERN_DB_AVAILABLE = True
except ImportError:
    PATTERN_DB_AVAILABLE = False

# Sparrow 네스팅 (State-of-the-art)
try:
    import spyrrow
    SPARROW_AVAILABLE = True
except ImportError:
    SPARROW_AVAILABLE = False


def run_sparrow_nesting(pattern_data, width_cm, time_limit, allow_rotation, spacing, allow_mirror=False, buffer_mm=0):
    """
    Sparrow 네스팅 실행

    Args:
        pattern_data: 패턴 데이터 리스트 [{coords_cm, quantity, pattern_id, area_cm2}, ...]
        width_cm: 원단 폭 (cm)
        time_limit: 최적화 시간 제한 (초)
        allow_rotation: 180도 회전 허용 여부
        spacing: 패턴 간격 (mm) - cm로 변환하여 적용
        allow_mirror: 뒤집기(좌우 미러링) 허용 여부
        buffer_mm: 패턴 둘레 버퍼 (mm) - 패턴 외곽으로 확장하여 블로킹

    Returns:
        네스팅 결과 딕셔너리
    """
    # spyrrow Item 생성
    items = []
    total_area = 0
    item_idx = 0
    buffer_cm = buffer_mm / 10  # mm -> cm

    # 원본 좌표 저장 (버퍼 시각화용)
    original_shapes = {}  # id -> 원본 좌표 (Sparrow 좌표계)
    grainline_data = {}  # id -> 그레인라인 좌표 (Sparrow 좌표계)

    for p in pattern_data:
        coords = list(p['coords_cm'])
        # 닫힌 폴리곤으로 변환
        if coords[0] != coords[-1]:
            coords = coords + [coords[0]]

        # 원본 좌표 저장 (버퍼 적용 전)
        original_coords = coords.copy()

        # 버퍼 적용 (패턴 둘레 확장)
        if buffer_cm > 0:
            from shapely.geometry import Polygon as ShapelyPolygon
            poly = ShapelyPolygon(coords)
            buffered_poly = poly.buffer(buffer_cm, join_style=2)  # join_style=2: mitre (각진 모서리)
            if buffered_poly.is_valid and not buffered_poly.is_empty:
                coords = list(buffered_poly.exterior.coords)

        # 좌표 변환: DXF(X=폭, Y=길이) → Sparrow(X=길이, Y=폭)
        # 입력시 X와 Y를 교환하여 Sparrow가 올바르게 처리하도록 함
        swapped_coords = [(y, x) for x, y in coords]
        swapped_original = [(y, x) for x, y in original_coords]  # 원본도 변환

        # 좌우 미러링된 좌표 생성 (Y축 반전) - 좌/우 패턴 짝 배치용
        # Sparrow 좌표계: X=마카길이, Y=원단폭
        # Y축 반전 = 원단폭 방향으로 뒤집기 = 좌우 마주보기
        ys = [c[1] for c in swapped_coords]
        center_y = (min(ys) + max(ys)) / 2
        mirrored_coords = [(x, 2 * center_y - y) for x, y in swapped_coords]
        mirrored_original = [(x, 2 * center_y - y) for x, y in swapped_original]  # 원본도 미러링

        # 그레인라인 좌표 변환 (Sparrow 좌표계)
        grainline_cm = p.get('grainline_cm')
        swapped_grainline = None
        mirrored_grainline = None
        if grainline_cm:
            gl_start, gl_end = grainline_cm
            # DXF(X,Y) → Sparrow(Y,X) 좌표 교환
            swapped_grainline = ((gl_start[1], gl_start[0]), (gl_end[1], gl_end[0]))
            # 미러링된 그레인라인 (Y축 반전)
            mirrored_grainline = (
                (gl_start[1], 2 * center_y - gl_start[0]),
                (gl_end[1], 2 * center_y - gl_end[0])
            )

        # 수량만큼 아이템 생성
        for q in range(p['quantity']):
            unique_id = f"{p['pattern_id']}_{item_idx}"

            # 180도 회전 옵션 (항상 독립 적용)
            orientations = [0, 180] if allow_rotation else [0]

            # 좌우 마주 보기 옵션 (180도 회전과 독립)
            if allow_mirror:
                use_coords = mirrored_coords if (q % 2 == 1) else swapped_coords
                use_original = mirrored_original if (q % 2 == 1) else swapped_original
                use_grainline = mirrored_grainline if (q % 2 == 1) else swapped_grainline
            else:
                use_coords = swapped_coords
                use_original = swapped_original
                use_grainline = swapped_grainline

            # 원본 좌표 저장
            original_shapes[unique_id] = use_original
            # 그레인라인 좌표 저장
            if use_grainline:
                grainline_data[unique_id] = use_grainline

            item = spyrrow.Item(
                id=unique_id,
                shape=use_coords,
                demand=1,
                allowed_orientations=orientations
            )
            items.append(item)
            total_area += p['area_cm2']
            item_idx += 1

    # StripPackingInstance 생성
    spacing_cm = spacing / 10  # mm -> cm
    instance = spyrrow.StripPackingInstance(
        name="nesting",
        strip_height=width_cm,
        items=items
    )

    # 솔버 설정
    config = spyrrow.StripPackingConfig(
        total_computation_time=time_limit,
        min_items_separation=spacing_cm if spacing_cm > 0 else None,
        seed=42
    )

    # 실행
    solution = instance.solve(config)

    # 결과 변환
    used_length_cm = solution.width
    efficiency = solution.density * 100

    # 배치 정보 추출
    # PlacedItem: id, rotation, translation (x, y)
    placements = []
    item_shapes = {item.id: item.shape for item in items}  # ID -> 버퍼 좌표 매핑

    for placed in solution.placed_items:
        buffered_shape = item_shapes.get(placed.id, [])
        original_shape = original_shapes.get(placed.id, [])  # 원본 좌표 (버퍼 전)
        rotation = placed.rotation
        tx, ty = placed.translation

        # 180도 회전 비허용 시: 180도 회전된 패턴을 원래대로 되돌림
        # spyrrow가 allowed_orientations=[0]을 무시하는 경우 대응
        if not allow_rotation and abs(rotation - 180) < 1:
            rotation = 0
            # 180도 회전 취소를 위해 translation 보정
            # 패턴 중심 기준으로 180도 역회전 필요
            xs = [c[0] for c in buffered_shape]
            ys = [c[1] for c in buffered_shape]
            cx = (min(xs) + max(xs)) / 2
            cy = (min(ys) + max(ys)) / 2
            # 원래 회전 중심에서의 오프셋 계산 후 역보정
            tx = tx + 2 * cx
            ty = ty + 2 * cy

        # 회전 및 이동 적용하여 최종 좌표 계산
        # Sparrow 좌표계 (입력 교환 후): X=마카길이, Y=원단폭
        # 출력시 다시 교환: X=원단폭, Y=마카길이
        cos_r = math.cos(math.radians(rotation))
        sin_r = math.sin(math.radians(rotation))

        # 버퍼 적용된 좌표 변환
        transformed_coords = []
        for x, y in buffered_shape:
            # 회전 (원점 기준)
            rx = x * cos_r - y * sin_r
            ry = x * sin_r + y * cos_r
            # 이동
            px = rx + tx
            py = ry + ty
            # 좌표 교환: Sparrow(X=길이, Y=폭) → 시각화(X=폭, Y=길이)
            transformed_coords.append((py, px))

        # 원본 좌표 변환 (버퍼 전)
        original_transformed = []
        for x, y in original_shape:
            # 회전 (원점 기준)
            rx = x * cos_r - y * sin_r
            ry = x * sin_r + y * cos_r
            # 이동
            px = rx + tx
            py = ry + ty
            # 좌표 교환: Sparrow(X=길이, Y=폭) → 시각화(X=폭, Y=길이)
            original_transformed.append((py, px))

        # 그레인라인 좌표 변환
        grainline_transformed = None
        if placed.id in grainline_data:
            gl_start, gl_end = grainline_data[placed.id]
            transformed_gl = []
            for x, y in [gl_start, gl_end]:
                # 회전 (원점 기준)
                rx = x * cos_r - y * sin_r
                ry = x * sin_r + y * cos_r
                # 이동
                px = rx + tx
                py = ry + ty
                # 좌표 교환: Sparrow(X=길이, Y=폭) → 시각화(X=폭, Y=길이)
                transformed_gl.append((py, px))
            grainline_transformed = (transformed_gl[0], transformed_gl[1])

        placements.append({
            'pattern_id': placed.id,
            'x': ty,  # Sparrow Y → 시각화 X (폭 방향)
            'y': tx,  # Sparrow X → 시각화 Y (길이 방향)
            'rotation': rotation,
            'coords': transformed_coords,  # 버퍼 적용된 좌표
            'original_coords': original_transformed,  # 원본 좌표 (버퍼 전)
            'grainline': grainline_transformed  # 그레인라인 좌표
        })

    return {
        'success': True,
        'placed_count': len(placements),
        'total_count': len(items),
        'used_length': used_length_cm * 10,  # cm -> mm (호환성)
        'used_length_mm': used_length_cm * 10,  # mm (시각화용)
        'used_length_cm': used_length_cm,
        'used_length_yd': used_length_cm / 100 * 1.09361,
        'efficiency': round(efficiency, 1),
        'placements': placements,
        'sparrow_mode': True,
        'buffer_mm': buffer_mm  # 버퍼 크기 저장
    }


def create_sparrow_visualization(result, sheet_width_cm):
    """Sparrow 네스팅 결과 시각화"""
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.path import Path
    import matplotlib.font_manager as fm
    import platform

    # 한글 폰트 설정 (크로스 플랫폼)
    if platform.system() == 'Windows':
        plt.rcParams['font.family'] = 'Malgun Gothic'
    else:
        # Linux: NanumGothic 우선, 없으면 DejaVu Sans
        available_fonts = [f.name for f in fm.fontManager.ttflist]
        if 'NanumGothic' in available_fonts:
            plt.rcParams['font.family'] = 'NanumGothic'
        elif 'Nanum Gothic' in available_fonts:
            plt.rcParams['font.family'] = 'Nanum Gothic'
        else:
            plt.rcParams['font.family'] = 'DejaVu Sans'
    plt.rcParams['axes.unicode_minus'] = False

    if not result.get('success'):
        return None

    fig_width = 12
    used_length_cm = result['used_length_cm']
    aspect = used_length_cm / sheet_width_cm
    fig_height = max(3, min(15, fig_width * aspect * 0.4))

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    # 시트 배경
    sheet = patches.Rectangle(
        (0, 0), sheet_width_cm, used_length_cm,
        linewidth=2, edgecolor='green', facecolor='#e8f5e9', alpha=0.5
    )
    ax.add_patch(sheet)

    # 색상 팔레트
    colors = plt.cm.Set3(range(12))

    # 사이즈 개수 확인
    all_sizes = st.session_state.get('all_sizes', [])
    selected_sizes = st.session_state.get('selected_sizes', all_sizes)
    has_multiple_sizes = len(selected_sizes) >= 2

    if has_multiple_sizes:
        # 사이즈 2개 이상: 같은 사이즈는 같은 색상
        # pattern_id에 저장된 사이즈는 4자로 잘려있으므로 앞 4자로 매핑
        size_color_map = {}
        for i, size in enumerate(selected_sizes):
            size_color_map[size] = colors[i % len(colors)]
            size_color_map[size[:4]] = colors[i % len(colors)]  # 잘린 버전도 매핑
    else:
        # 사이즈 1개: 같은 패턴은 같은 색상
        unique_patterns = list(set(p['pattern_id'].split('\n')[0] for p in result['placements']))
        pattern_color_map = {name: colors[i % len(colors)] for i, name in enumerate(unique_patterns)}

    # 버퍼 사용 여부 확인
    buffer_mm = result.get('buffer_mm', 0)
    has_buffer = buffer_mm > 0

    # 패턴 그리기
    for i, p in enumerate(result['placements']):
        coords = p['coords']  # 버퍼 적용된 좌표
        original_coords = p.get('original_coords', [])  # 원본 좌표

        if coords:
            # coords는 이미 배치된 좌표
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]

            # 색상 결정
            if has_multiple_sizes:
                # 사이즈별 색상 (pattern_id 형식: "패턴명\n사이즈_인덱스")
                parts = p['pattern_id'].split('\n')
                if len(parts) > 1:
                    # "사이즈_인덱스"에서 사이즈만 추출 (마지막 _인덱스 제거)
                    size_with_idx = parts[1].strip()
                    size_part = size_with_idx.rsplit('_', 1)[0] if '_' in size_with_idx else size_with_idx
                else:
                    size_part = ''
                # 매핑에서 색상 찾기
                color = size_color_map.get(size_part, colors[i % len(colors)])
            else:
                # 패턴별 색상
                pattern_name = p['pattern_id'].split('\n')[0]
                color = pattern_color_map.get(pattern_name, colors[0])

            # 버퍼가 있으면 버퍼 영역(연한 색) + 원본 패턴(진한 색) 표시
            if has_buffer and original_coords:
                # 버퍼 영역 (연한 색, 점선 테두리)
                ax.fill(xs, ys, alpha=0.3, facecolor=color, edgecolor='gray', linewidth=0.5, linestyle='--')

                # 원본 패턴 (진한 색, 실선 테두리)
                orig_xs = [c[0] for c in original_coords]
                orig_ys = [c[1] for c in original_coords]
                ax.fill(orig_xs, orig_ys, alpha=0.7, facecolor=color, edgecolor='black', linewidth=0.5)
            else:
                # 버퍼 없으면 기존대로 표시
                ax.fill(xs, ys, alpha=0.7, facecolor=color, edgecolor='black', linewidth=0.5)

            # 패턴 ID 표시 (중심점 계산 - Shapely centroid 사용)
            from shapely.geometry import Polygon as ShapelyPoly
            try:
                poly_shape = ShapelyPoly(coords)
                centroid = poly_shape.centroid
                cx, cy = centroid.x, centroid.y
            except:
                cx = sum(xs) / len(xs)
                cy = sum(ys) / len(ys)
            # 패턴ID에서 _인덱스 제거하여 "구분12자\n사이즈4자" 형식 유지
            raw_id = p['pattern_id']
            if '_' in raw_id.split('\n')[-1]:
                # 마지막 _인덱스 제거
                parts = raw_id.rsplit('_', 1)
                label = parts[0]
            else:
                label = raw_id
            ax.text(cx, cy, label, ha='center', va='center', fontsize=4, fontweight='bold')

            # 그레인라인 표시 (검정 실선, 크기 50%)
            grainline = p.get('grainline')
            if grainline:
                gl_start, gl_end = grainline
                # 그레인라인 크기를 50%로 축소 (중심점 기준)
                cx_gl = (gl_start[0] + gl_end[0]) / 2
                cy_gl = (gl_start[1] + gl_end[1]) / 2
                gl_start_scaled = (cx_gl + (gl_start[0] - cx_gl) * 0.5, cy_gl + (gl_start[1] - cy_gl) * 0.5)
                gl_end_scaled = (cx_gl + (gl_end[0] - cx_gl) * 0.5, cy_gl + (gl_end[1] - cy_gl) * 0.5)
                ax.plot([gl_start_scaled[0], gl_end_scaled[0]], [gl_start_scaled[1], gl_end_scaled[1]],
                        'k-', linewidth=0.4, alpha=0.8)
                # 화살표 머리 (끝점에)
                dx = gl_end_scaled[0] - gl_start_scaled[0]
                dy = gl_end_scaled[1] - gl_start_scaled[1]
                arrow_scale = 0.15
                ax.annotate('', xy=(gl_end_scaled[0], gl_end_scaled[1]),
                            xytext=(gl_end_scaled[0] - dx*arrow_scale, gl_end_scaled[1] - dy*arrow_scale),
                            arrowprops=dict(arrowstyle='->', color='black', lw=0.4))

    ax.set_xlim(-1, sheet_width_cm + 1)
    ax.set_ylim(-1, used_length_cm + 1)
    ax.set_aspect('equal')
    ax.set_xlabel('폭 (cm)')
    ax.set_ylabel('길이 (cm)')

    plt.tight_layout()
    return fig

# ==============================================================================
# 1. 페이지 및 스타일 설정 (Configuration & CSS)
# ==============================================================================
st.set_page_config(
    page_title="스마트 요척 산출서",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 커스텀 CSS 스타일 정의
st.markdown("""
<style>
    /* 상단 여백 조정 - 제목이 잘리지 않도록 */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
        overflow: visible;
    }

    /* Streamlit 기본 헤더 숨기기 (여백 확보) */
    header[data-testid="stHeader"] {
        height: 0;
        min-height: 0;
        padding: 0;
        visibility: hidden;
    }

    /* 컴포넌트 간격 조정 */
    div[data-testid="stVerticalBlock"] > div { gap: 0rem; }
    div[data-testid="stColumn"] { text-align: center; }
    
    /* 숫자 버튼 스타일 (클릭 영역 확보) */
    div[data-testid="stColumn"] button {
        width: 100%;
        border: 1px solid #ccc;
        font-weight: bold;
        background-color: #f9f9f9;
        font-size: 13px;
        padding: 0px;
        margin-top: 2px;
        height: 28px;
    }
    div[data-testid="stColumn"] button:hover {
        border-color: #0068c9;
        color: #0068c9;
        background-color: #eef5ff;
    }
    
    /* 체크박스 중앙 정렬 보정 */
    div[data-testid="stColumn"] div[data-testid="stCheckbox"] {
        display: flex;
        justify-content: center;
        margin-top: -2px;
    }
    
    /* 기본 툴바 및 풀스크린 버튼 숨기기 (깔끔한 UI 유지) */
    [data-testid="stElementToolbar"] { display: none !important; }
    button[title="View fullscreen"] { display: none !important; }
    
    /* 요척 결과 카드 입력창 컴팩트 스타일 */
    div[data-testid="stNumberInput"] input {
        padding: 0px 5px;
        height: 30px;
        font-size: 13px;
        text-align: center;
    }

    /* 숫자 입력 플러스/마이너스 버튼 크기 (60%) */
    div[data-testid="stNumberInput"] button {
        width: 24px !important;
        min-width: 24px !important;
        padding: 0 6px !important;
    }
    div[data-testid="stNumberInput"] button svg {
        width: 12px !important;
        height: 12px !important;
    }

    /* 네스팅 결과 expander 제목 크기 25% 확대 */
    div[data-testid="stExpander"] summary span {
        font-size: 1.25em !important;
        font-weight: bold !important;
    }

    /* 네스팅 결과 메트릭 - 제목/데이터 모두 가운데 정렬 */
    div[data-testid="stExpander"] div[data-testid="stMetric"] {
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        text-align: center !important;
    }
    div[data-testid="stExpander"] div[data-testid="stMetric"] label {
        font-size: 0.7rem !important;
        width: 100% !important;
        text-align: center !important;
    }
    div[data-testid="stExpander"] div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        font-size: 1rem !important;
        width: 100% !important;
        text-align: center !important;
    }

    /* file_uploader 드래그앤드롭 영역 스타일 강화 */
    [data-testid="stFileUploader"],
    .stFileUploader {
        border: 3px dashed #0068c9 !important;
        border-radius: 15px !important;
        padding: 15px !important;
        background: linear-gradient(135deg, #f0f7ff 0%, #e8f4f8 100%) !important;
    }
    [data-testid="stFileUploader"] section,
    .stFileUploader section {
        min-height: 200px !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
    }
    [data-testid="stFileUploader"] label,
    .stFileUploader label {
        font-size: 16px !important;
        font-weight: bold !important;
        color: #0068c9 !important;
    }
    [data-testid="stFileUploader"] small,
    .stFileUploader small {
        font-size: 13px !important;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("<h1 style='font-size: 1.8rem; margin-top: 0; margin-bottom: 0.5rem; white-space: nowrap;'>👕 스마트 의류 요척 산출서</h1>", unsafe_allow_html=True)

# Streamlit 버전 호환성 체크 (팝업창 기능용)
try:
    st_version = st.__version__
    major, minor = map(int, st_version.split('.')[:2])
    if major < 1 or (major == 1 and minor < 34):
        st.error(f"🚨 중요: 현재 Streamlit 버전({st_version})이 낮습니다. 터미널에 'pip install --upgrade streamlit'을 실행해주세요.")
        st.stop()
except: pass


# ==============================================================================
# 2. 헬퍼 함수 및 유틸리티 (Helpers)
# ==============================================================================

def export_nesting_to_excel(nesting_results, timestamp):
    """네스팅 결과를 엑셀로 내보내기 (한 시트에 모든 데이터 순서대로)"""
    from io import BytesIO
    import pandas as pd
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils.dataframe import dataframe_to_rows
    import matplotlib.pyplot as plt

    output = BytesIO()
    wb = Workbook()
    ws = wb.active
    ws.title = "네스팅결과"

    # 스타일 정의
    header_font = Font(bold=True, size=12)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font_white = Font(bold=True, size=12, color="FFFFFF")
    section_font = Font(bold=True, size=14)
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    current_row = 1

    # === 1. 마카 요약 섹션 ===
    ws.cell(row=current_row, column=1, value="■ 마카 요약").font = section_font
    current_row += 1

    summary_headers = ['원단', '벌수', '패턴수', '원단폭(cm)', '마카길이(cm)', '요척(YD)', '효율(%)', '작업일시']
    for col, header in enumerate(summary_headers, 1):
        cell = ws.cell(row=current_row, column=col, value=header)
        cell.font = header_font_white
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='center')
    current_row += 1

    for fabric, result in nesting_results.items():
        if result.get('success'):
            marker_qty = result.get('marker_quantity', 1)
            yield_per_set = result.get('used_length_yd', 0) / marker_qty
            row_data = [
                fabric,
                marker_qty,
                f"{result.get('placed_count', 0)}/{result.get('total_count', 0)}",
                result.get('width_cm', 0),
                round(result.get('used_length_cm', 0), 1),
                round(yield_per_set, 2),
                result.get('efficiency', 0),
                timestamp
            ]
            for col, value in enumerate(row_data, 1):
                cell = ws.cell(row=current_row, column=col, value=value)
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='center')
            current_row += 1

    current_row += 2  # 빈 줄 추가

    # === 2. 원단별 마카 이미지 + 배치 상세 (2열 배치) ===
    ws.cell(row=current_row, column=1, value="■ 마카 이미지").font = section_font
    current_row += 1

    fabric_list = [f for f, r in nesting_results.items() if r.get('success')]

    # 배치 상세 테이블 추가 함수
    def add_placement_table(start_row, start_col, fabric, result):
        """마카 아래에 배치 상세 테이블 추가"""
        row = start_row
        if result.get('placements'):
            ws.cell(row=row, column=start_col, value=f"배치 상세").font = Font(bold=True, size=9)
            row += 1

            placement_headers = ['번호', '패턴ID', 'X', 'Y', '회전']
            for col_offset, header in enumerate(placement_headers):
                cell = ws.cell(row=row, column=start_col + col_offset, value=header)
                cell.font = Font(bold=True, size=8, color="FFFFFF")
                cell.fill = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='center')
            row += 1

            for pi, p in enumerate(result['placements']):
                row_data = [
                    pi + 1,
                    p.get('pattern_id', ''),
                    round(p.get('x', 0), 1),
                    round(p.get('y', 0), 1),
                    p.get('rotation', 0)
                ]
                for col_offset, value in enumerate(row_data):
                    cell = ws.cell(row=row, column=start_col + col_offset, value=value)
                    cell.border = thin_border
                    cell.alignment = Alignment(horizontal='center')
                    cell.font = Font(size=8)
                row += 1
        return row

    # 2개씩 묶어서 처리
    for i in range(0, len(fabric_list), 2):
        row_start = current_row
        img_rows1 = 0
        img_rows2 = 0

        # 왼쪽 마카 (A열)
        fabric1 = fabric_list[i]
        result1 = nesting_results[fabric1]
        ws.cell(row=current_row, column=1, value=f"▷ {fabric1}").font = Font(bold=True, size=11)

        try:
            width_cm = result1.get('width_cm', 150)
            if result1.get('sparrow_mode'):
                fig = create_sparrow_visualization(result1, width_cm)
            else:
                fig = create_nesting_visualization(result1, width_cm)

            if fig:
                img_buffer = BytesIO()
                fig.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight',
                           facecolor='white', edgecolor='none')
                img_buffer.seek(0)
                plt.close(fig)

                img = XLImage(img_buffer)
                orig_width = img.width
                orig_height = img.height
                if orig_width > 0:
                    img.width = 450
                    img.height = int(orig_height * (450 / orig_width))

                ws.add_image(img, f'A{current_row + 1}')
                img_rows1 = int(img.height / 15) + 2
        except Exception as e:
            ws.cell(row=current_row + 1, column=1, value=f"오류: {e}")
            img_rows1 = 3

        # 오른쪽 마카 (F열) - 있으면
        fabric2 = None
        result2 = None
        if i + 1 < len(fabric_list):
            fabric2 = fabric_list[i + 1]
            result2 = nesting_results[fabric2]
            ws.cell(row=current_row, column=6, value=f"▷ {fabric2}").font = Font(bold=True, size=11)

            try:
                width_cm = result2.get('width_cm', 150)
                if result2.get('sparrow_mode'):
                    fig = create_sparrow_visualization(result2, width_cm)
                else:
                    fig = create_nesting_visualization(result2, width_cm)

                if fig:
                    img_buffer = BytesIO()
                    fig.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight',
                               facecolor='white', edgecolor='none')
                    img_buffer.seek(0)
                    plt.close(fig)

                    img = XLImage(img_buffer)
                    orig_width = img.width
                    orig_height = img.height
                    if orig_width > 0:
                        img.width = 450
                        img.height = int(orig_height * (450 / orig_width))

                    ws.add_image(img, f'F{current_row + 1}')
                    img_rows2 = int(img.height / 15) + 2
            except Exception as e:
                ws.cell(row=current_row + 1, column=6, value=f"오류: {e}")
                img_rows2 = 3

        # 마카 이미지 아래로 이동
        max_img_rows = max(img_rows1, img_rows2, 10)
        current_row += max_img_rows + 2  # 2칸 아래

        # 왼쪽 배치 상세 (A열)
        detail_start_row = current_row
        end_row1 = add_placement_table(current_row, 1, fabric1, result1)

        # 오른쪽 배치 상세 (F열) - 있으면
        end_row2 = current_row
        if fabric2 and result2:
            end_row2 = add_placement_table(current_row, 6, fabric2, result2)

        current_row = max(end_row1, end_row2) + 2

    # 열 너비 조정
    ws.column_dimensions['A'].width = 15
    ws.column_dimensions['B'].width = 12
    ws.column_dimensions['C'].width = 12
    ws.column_dimensions['D'].width = 14
    ws.column_dimensions['E'].width = 14
    ws.column_dimensions['F'].width = 12
    ws.column_dimensions['G'].width = 10
    ws.column_dimensions['H'].width = 18

    wb.save(output)
    output.seek(0)
    return output.getvalue()


def get_fabric_color_hex(fabric_name):
    """원단 이름에 따른 색상 코드를 반환합니다."""
    color_map = {
        "겉감": "#4c78a8",  # Blue (Tableau)
        "안감": "#e45756",  # Red (Tableau)
        "심지": "#edc948",  # Yellow (Tableau) - User Request
        "배색": "#f58518",  # Orange (Tableau)
        "주머니": "#54a24b" # Green (Tableau)
    }
    for key, color in color_map.items():
        if key in fabric_name: return color
    return "#dddddd" # 기본값 (회색)

def extract_lines(entity, lines_list):
    """DXF 엔티티에서 선분 정보를 재귀적으로 추출합니다."""
    dxftype = entity.dxftype()
    try:
        if dxftype == 'LINE':
            start, end = entity.dxf.start, entity.dxf.end
            lines_list.append(LineString([(start.x, start.y), (end.x, end.y)]))
        elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
            points = list(entity.points())
            if len(points) > 1:
                lines_list.append(LineString([(p[0], p[1]) for p in points]))
        elif dxftype in ['SPLINE', 'ARC', 'CIRCLE', 'ELLIPSE']:
            path = ezdxf.path.make_path(entity)
            vertices = list(path.flattening(distance=1.0))
            if len(vertices) > 1:
                lines_list.append(LineString([(v.x, v.y) for v in vertices]))
        elif dxftype == 'INSERT':
            # 블록 참조(Insert)일 경우 내부 엔티티 탐색
            for virtual_entity in entity.virtual_entities():
                extract_lines(virtual_entity, lines_list)
    except Exception:
        pass # 파싱 불가능한 엔티티는 무시

def check_is_fold(poly):
    """패턴이 골선(Fold)인지 판별합니다. (폭/높이가 매우 좁고 긴 형태)"""
    minx, miny, maxx, maxy = poly.bounds
    full_h, full_w = maxy - miny, maxx - minx
    coords = list(poly.exterior.coords)
    for i in range(len(coords)-1):
        p1, p2 = coords[i], coords[i+1]
        dist = math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
        # 수직골 또는 수평골 판별 로직
        # if abs(p1[0]-p2[0]) < 0.1 and dist > full_h * 0.9: return True, "수직골" (사용자 요청으로 삭제 - 대칭 로직으로 대체)
        # if abs(p1[1]-p2[1]) < 0.1 and dist > full_w * 0.9: return True, "수평골" (사용자 요청으로 삭제)
    return False, "일반"

def poly_to_base64(poly, fill_color='gray'):
    """Shapely Polygon을 정사각형 썸네일 이미지(Base64)로 변환합니다."""
    fig, ax = plt.subplots(figsize=(1, 1))
    x, y = poly.exterior.xy
    ax.plot(x, y, 'k-', lw=2)
    ax.fill(x, y, fill_color, alpha=0.6) # 색상 적용 (투명도 약간 높임)
    ax.axis('off')

    # 정사각형 비율 맞추기 (Centering)
    minx, miny, maxx, maxy = poly.bounds
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    max_dim = max(maxx - minx, maxy - miny)
    padding = max_dim * 0.1 # 10% 여백
    span = (max_dim + padding) / 2

    ax.set_xlim(cx - span, cx + span)
    ax.set_ylim(cy - span, cy + span)
    ax.set_aspect('equal')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0)
    plt.close(fig)

    data = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{data}"


def get_cached_thumbnail(idx, poly, fabric_name, zoom_span, grainline_info=None):
    """
    썸네일 캐싱 함수: (폴리곤 고유ID, 원단명, zoom_span) 조합으로 캐시 관리
    원단명 또는 zoom_span이 변경되면 해당 썸네일만 새로 생성
    복사/정렬 후에도 폴리곤 형상으로 정확히 매칭
    패턴 삭제 시 zoom_span 변경으로 자동 재생성
    그레인라인이 있으면 빨간 점선으로 표시
    """
    # 캐시 초기화
    if 'thumbnail_cache' not in st.session_state:
        st.session_state.thumbnail_cache = {}

    # 캐시 키: (폴리곤 면적 + 중심점 해시, 원단명, zoom_span, grainline 유무) - 폴리곤 형상 + 크기 기반 고유 식별
    poly_id = (round(poly.area, 2), round(poly.centroid.x, 2), round(poly.centroid.y, 2))
    zoom_key = round(zoom_span, 1)  # zoom_span 변경 감지
    has_grainline = grainline_info is not None
    cache_key = (poly_id, fabric_name, zoom_key, has_grainline)

    # 캐시에 있으면 재사용
    if cache_key in st.session_state.thumbnail_cache:
        return st.session_state.thumbnail_cache[cache_key]

    # 없으면 새로 생성
    fig, ax = plt.subplots(figsize=(1, 1))
    x, y = poly.exterior.xy
    ax.plot(x, y, 'k-', lw=0.5)
    ax.fill(x, y, color=get_fabric_color_hex(fabric_name), alpha=0.6)

    # 그레인라인 - 일괄수정도구 썸네일에서는 숨김 처리
    pass

    ax.set_xlim(poly.centroid.x - zoom_span/2, poly.centroid.x + zoom_span/2)
    ax.set_ylim(poly.centroid.y - zoom_span/2, poly.centroid.y + zoom_span/2)
    ax.set_aspect('equal')
    ax.axis('off')

    # BytesIO로 이미지 저장
    buf = io.BytesIO()
    fig.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0, dpi=72)
    plt.close(fig)
    buf.seek(0)

    # 캐시에 저장
    st.session_state.thumbnail_cache[cache_key] = buf.getvalue()

    return st.session_state.thumbnail_cache[cache_key]


def create_overlay_visualization(patterns_group, selected_sizes, all_sizes, global_max_dim=None):
    """
    동일 패턴 그룹의 여러 사이즈를 중첩하여 시각화
    - 바탕색 없이 외곽선만 표시
    - 사이즈별 다른 색상 외곽선
    - 중심 정렬로 크기 비교

    Args:
        patterns_group: [(poly, pattern_name, fabric_name, size_name, pattern_group), ...] 동일 그룹
        selected_sizes: 선택된 사이즈 목록
        all_sizes: 전체 사이즈 목록
        global_max_dim: 전역 최대 크기 (모든 패턴에 동일 비율 적용)

    Returns:
        matplotlib figure
    """
    import matplotlib.pyplot as plt
    import matplotlib.lines as mlines

    fig, ax = plt.subplots(figsize=(6, 6))  # 패턴 크게 표시

    # 사이즈별 색상 (파랑→빨강 그라데이션)
    size_colors = {}
    cmap = plt.cm.get_cmap('coolwarm', len(all_sizes) + 1)
    for i, size in enumerate(all_sizes):
        size_colors[size] = cmap(i / max(len(all_sizes) - 1, 1))

    # 모든 패턴의 경계 계산 (중심 맞추기용)
    all_bounds = []
    for p_data in patterns_group:
        poly = p_data[0]  # 첫 번째 요소가 poly
        all_bounds.append(poly.bounds)

    if not all_bounds:
        return fig

    # 전체 영역 계산
    min_x = min(b[0] for b in all_bounds)
    min_y = min(b[1] for b in all_bounds)
    max_x = max(b[2] for b in all_bounds)
    max_y = max(b[3] for b in all_bounds)

    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    # 사이즈 순서대로 그리기 (큰 것부터)
    sorted_patterns = sorted(patterns_group, key=lambda x: x[0].area, reverse=True)

    legend_handles = []
    drawn_sizes = set()

    for p_data in sorted_patterns:
        poly = p_data[0]
        size_name = p_data[3]  # 4번째 요소

        if not size_name:
            continue

        # 원본 DXF 위치 그대로 사용 (중심 이동 없음)
        x, y = poly.exterior.xy

        # 사이즈별 색상
        color = size_colors.get(size_name, 'gray')
        is_selected = size_name in selected_sizes

        # 선택된 사이즈: 실선, 미선택: 점선+흐리게
        if is_selected:
            ax.plot(x, y, color=color, linewidth=1.0, linestyle='-', alpha=1.0)
        else:
            ax.plot(x, y, color=color, linewidth=0.1, linestyle='--', alpha=0.4)

        # 범례용 핸들 (사이즈당 하나만)
        if size_name not in drawn_sizes:
            drawn_sizes.add(size_name)
            line = mlines.Line2D([], [], color=color,
                                 linewidth=1.0 if is_selected else 0.1,
                                 linestyle='-' if is_selected else '--',
                                 alpha=1.0 if is_selected else 0.5,
                                 label=size_name)
            legend_handles.append(line)

    # 축 설정 - 전역 최대 크기 사용 (모든 패턴 동일 비율)
    if global_max_dim:
        # 전역 크기 기준으로 뷰포트 설정
        margin = global_max_dim * 0.15
        half_dim = global_max_dim / 2 + margin
        ax.set_xlim(center_x - half_dim, center_x + half_dim)
        ax.set_ylim(center_y - half_dim, center_y + half_dim)
    else:
        # 개별 패턴 크기 기준 (기존 방식)
        margin = max(max_x - min_x, max_y - min_y) * 0.15
        ax.set_xlim(min_x - margin, max_x + margin)
        ax.set_ylim(min_y - margin, max_y + margin)
    ax.set_aspect('equal')
    ax.axis('off')

    # 범례 (사이즈 순서대로)
    if legend_handles:
        sorted_handles = sorted(legend_handles, key=lambda x: x.get_label())
        ax.legend(handles=sorted_handles, loc='upper right', fontsize=8,
                  framealpha=0.9, handlelength=2.0)

    plt.tight_layout()
    return fig


def check_symmetry(poly):
    """
    패턴의 대칭 여부를 판단합니다. (좌우대칭 or 상하대칭)
    원리: 중심축 기준으로 반전시켰을 때 원본과 거의 겹치는지(차집합 면적이 적은지) 확인
    """
    try:
        # 허용 오차 (전체 면적의 2% 미만 차이면 대칭으로 간주 - 98% 일치)
        tolerance = poly.area * 0.02 
        
        # 1. 좌우 대칭 확인 (Horizontal Reflection)
        reflected_h = affinity.scale(poly, xfact=-1, origin='centroid')
        diff_h = poly.symmetric_difference(reflected_h).area
        if diff_h < tolerance:
            return True, "좌우대칭"

        # 2. 상하 대칭 확인 (Vertical Reflection)
        reflected_v = affinity.scale(poly, yfact=-1, origin='centroid')
        diff_v = poly.symmetric_difference(reflected_v).area
        if diff_v < tolerance:
            return True, "상하대칭"
            
        return False, "비대칭"
    except:
        return False, "오류"


def check_vertical_straight_edge(poly, min_length_cm=50):
    """
    패턴의 세로 직선(좌측/우측)이 특정 길이 이상인지 판별합니다.
    min_length_cm 이상인 세로 직선이 1개라도 있으면 True 반환
    """
    try:
        coords = list(poly.exterior.coords)
        if len(coords) < 4:
            return False

        min_length_mm = min_length_cm * 10  # cm → mm 변환

        # 연속된 점들 사이의 세로 직선 확인
        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]

            # X좌표 변화가 거의 없으면 세로 직선
            x_diff = abs(x2 - x1)
            y_length = abs(y2 - y1)

            # X좌표 변화가 전체 폭의 2% 이내이고, 세로 길이가 min_length 이상
            minx, miny, maxx, maxy = poly.bounds
            width = maxx - minx

            if x_diff < width * 0.02 and y_length >= min_length_mm:
                return True

        return False
    except:
        return False


def check_horizontal_straight_edge(poly):
    """
    패턴의 가로 직선(상단/하단)이 1개 이상 있는지 판별합니다.
    상단 또는 하단 중 Y좌표 변화가 1% 이내인 직선이 있으면 True 반환
    """
    try:
        coords = list(poly.exterior.coords)
        if len(coords) < 4:
            return False

        minx, miny, maxx, maxy = poly.bounds
        height = maxy - miny

        # 상단/하단 영역 정의 (전체 높이의 10% 이내)
        top_threshold = maxy - height * 0.1
        bottom_threshold = miny + height * 0.1

        top_points = [(x, y) for x, y in coords if y >= top_threshold]
        bottom_points = [(x, y) for x, y in coords if y <= bottom_threshold]

        def is_straight_line(points):
            if len(points) < 2:
                return False
            y_values = [p[1] for p in points]
            y_range = max(y_values) - min(y_values)
            return y_range < height * 0.01

        return is_straight_line(top_points) or is_straight_line(bottom_points)
    except:
        return False


def check_parallel_edges(poly, similarity_threshold=0.85):
    """
    패턴의 상하단 가로선이 평행선인지 판별합니다.
    상하단 가로 길이 비율이 similarity_threshold 이상이면 True 반환
    """
    try:
        coords = list(poly.exterior.coords)
        if len(coords) < 4:
            return False

        minx, miny, maxx, maxy = poly.bounds
        height = maxy - miny

        # 상단/하단 영역 정의 (전체 높이의 10% 이내)
        top_threshold = maxy - height * 0.1
        bottom_threshold = miny + height * 0.1

        top_points = [(x, y) for x, y in coords if y >= top_threshold]
        bottom_points = [(x, y) for x, y in coords if y <= bottom_threshold]

        def get_edge_length(points):
            if len(points) < 2:
                return 0
            x_values = [p[0] for p in points]
            return max(x_values) - min(x_values)

        top_length = get_edge_length(top_points)
        bottom_length = get_edge_length(bottom_points)

        if top_length > 0 and bottom_length > 0:
            similarity = min(top_length, bottom_length) / max(top_length, bottom_length)
            return similarity >= similarity_threshold

        return False
    except:
        return False


# ==============================================================================
# 3. 핵심 로직: DXF 처리 (Core Logic)
# ==============================================================================

from shapely.ops import polygonize, linemerge

# ... (기존 extract_lines 함수는 그대로 유지하거나 필요시 수정) ...

@st.cache_data
def extract_style_no(file_path):
    """DXF 파일에서 스타일번호를 추출합니다."""
    try:
        try:
            doc = ezdxf.readfile(file_path, encoding='cp949')
        except:
            doc = ezdxf.readfile(file_path)

        msp = doc.modelspace()

        for entity in msp:
            if entity.dxftype() == 'INSERT':
                block = doc.blocks.get(entity.dxf.name)
                for be in block:
                    if be.dxftype() == 'TEXT':
                        text = be.dxf.text
                        text_upper = text.upper()
                        # 스타일번호: S/#..., M/#... 형식 (대소문자 무시)
                        if text_upper.startswith('ANNOTATION:') and '/#' in text:
                            val = text.split(':', 1)[1].strip()
                            # S/#5535-731 → 5535-731
                            if '/#' in val:
                                return val.split('/#')[1]
                break  # 첫 번째 블록에서만 추출
    except:
        pass
    return ""


def detect_grainline(block, return_coords=False):
    """
    블록 내에서 그레인라인(결방향)을 감지합니다.

    그레인라인 감지 방법:
    1. 레이어명에 'GRAIN', 'GL', '결', '7' 등 포함된 엔티티
    2. 그레인라인 레이어가 없으면 블록 내 독립 LINE 중 가장 긴 것 (100단위 이상)

    Args:
        block: ezdxf 블록 객체
        return_coords: True면 (angle, start, end) 반환, False면 angle만 반환

    Returns:
        return_coords=False: angle (그레인라인 각도) 또는 None
        return_coords=True: (angle, start, end) 또는 (None, None, None)
    """
    import math

    # 그레인라인 레이어 키워드 (숫자 7도 포함 - 일부 CAD 시스템에서 사용)
    grainline_keywords = ['GRAIN', 'GL', 'GRAINLINE', '결', '결방향', 'STRAIGHT']
    grainline_layer_numbers = ['7']  # 숫자 레이어도 그레인라인일 수 있음
    candidate_lines = []

    for entity in block:
        layer_name = str(entity.dxf.layer).upper() if hasattr(entity.dxf, 'layer') else ''

        # 레이어명으로 그레인라인 감지 (키워드 또는 숫자 레이어)
        is_grainline_layer = (
            any(kw in layer_name for kw in grainline_keywords) or
            layer_name in grainline_layer_numbers
        )

        if entity.dxftype() == 'LINE':
            start = entity.dxf.start
            end = entity.dxf.end
            dx = end.x - start.x
            dy = end.y - start.y
            length = math.sqrt(dx*dx + dy*dy)

            if length > 1:  # 최소 길이 필터
                # 각도 계산 (수평 기준, -180 ~ 180)
                angle = math.degrees(math.atan2(dy, dx))
                candidate_lines.append({
                    'angle': angle,
                    'length': length,
                    'is_grainline_layer': is_grainline_layer,
                    'start': (start.x, start.y),
                    'end': (end.x, end.y)
                })

        elif entity.dxftype() == 'LWPOLYLINE' and not entity.closed:
            # 열린 폴리라인도 그레인라인일 수 있음
            pts = list(entity.get_points())
            if len(pts) == 2:  # 2점 직선
                start, end = pts[0], pts[1]
                dx = end[0] - start[0]
                dy = end[1] - start[1]
                length = math.sqrt(dx*dx + dy*dy)
                if length > 1:
                    angle = math.degrees(math.atan2(dy, dx))
                    candidate_lines.append({
                        'angle': angle,
                        'length': length,
                        'is_grainline_layer': is_grainline_layer,
                        'start': (start[0], start[1]),
                        'end': (end[0], end[1])
                    })

    if not candidate_lines:
        return (None, None, None) if return_coords else None

    # 그레인라인 우선순위:
    # 1. 그레인라인 레이어에 있는 선
    # 2. 그 중 가장 긴 선
    grainline_layer_lines = [l for l in candidate_lines if l['is_grainline_layer']]

    best = None
    if grainline_layer_lines:
        # 그레인라인 레이어에서 가장 긴 선
        best = max(grainline_layer_lines, key=lambda x: x['length'])
    else:
        # 그레인라인 레이어가 없으면 독립 LINE 중 가장 긴 것 사용 (100단위 이상)
        long_lines = [l for l in candidate_lines if l['length'] >= 100]
        if long_lines:
            best = max(long_lines, key=lambda x: x['length'])

    if best:
        if return_coords:
            return best['angle'], best['start'], best['end']
        return best['angle']

    return (None, None, None) if return_coords else None


def rotate_polygon_to_vertical_grain(poly, grainline_angle):
    """
    폴리곤을 그레인라인이 수직(90도)이 되도록 회전합니다.

    Args:
        poly: Shapely Polygon
        grainline_angle: 현재 그레인라인 각도 (도)

    Returns:
        회전된 Shapely Polygon
    """
    from shapely import affinity

    # 수직(90도)으로 맞추기 위해 필요한 회전 각도
    # grainline_angle이 0도면 90도 회전 필요
    # grainline_angle이 45도면 45도 회전 필요
    # grainline_angle이 90도면 0도 회전 (이미 수직)

    # 각도 정규화 (-90 ~ 90 범위로)
    normalized = grainline_angle % 180
    if normalized > 90:
        normalized -= 180

    # 수직(90도)까지 회전 필요 각도
    rotation_needed = 90 - normalized

    # 180도 이상 회전 방지
    if rotation_needed > 90:
        rotation_needed -= 180
    elif rotation_needed < -90:
        rotation_needed += 180

    if abs(rotation_needed) < 1:  # 이미 수직에 가까움
        return poly, 0

    # 중심점 기준 회전
    rotated = affinity.rotate(poly, rotation_needed, origin='centroid')
    return rotated, rotation_needed


def rotate_grainline_coords(start, end, rotation_angle, origin):
    """
    그레인라인 좌표를 회전시킵니다.

    Args:
        start: (x, y) 시작점
        end: (x, y) 끝점
        rotation_angle: 회전 각도 (도)
        origin: (x, y) 회전 중심점

    Returns:
        (new_start, new_end) 회전된 좌표
    """
    import math

    if abs(rotation_angle) < 1:
        return start, end

    rad = math.radians(rotation_angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    ox, oy = origin

    def rotate_point(px, py):
        dx = px - ox
        dy = py - oy
        new_x = ox + dx * cos_a - dy * sin_a
        new_y = oy + dx * sin_a + dy * cos_a
        return (new_x, new_y)

    new_start = rotate_point(start[0], start[1])
    new_end = rotate_point(end[0], end[1])
    return new_start, new_end


def detect_grainline_for_polygon(msp, poly):
    """
    모델스페이스에서 특정 폴리곤 내부 또는 근처에 있는 그레인라인을 감지합니다.
    레거시 DXF 파일용 (블록 없이 모델스페이스에 직접 그려진 패턴)

    Args:
        msp: ezdxf 모델스페이스
        poly: Shapely Polygon (대상 패턴)

    Returns:
        angle: 그레인라인의 각도 (도)
        None: 그레인라인을 찾지 못한 경우
    """
    import math
    from shapely.geometry import Point, LineString

    grainline_keywords = ['GRAIN', 'GL', 'GRAINLINE', '결', '결방향', 'STRAIGHT']
    candidate_lines = []

    # 폴리곤 바운딩박스 확장 (근처 선 검색용)
    bounds = poly.bounds
    margin = 50  # 50단위 여유
    search_bounds = (bounds[0] - margin, bounds[1] - margin,
                     bounds[2] + margin, bounds[3] + margin)

    for entity in msp:
        layer_name = entity.dxf.layer.upper() if hasattr(entity.dxf, 'layer') else ''
        is_grainline_layer = any(kw in layer_name for kw in grainline_keywords)

        if entity.dxftype() == 'LINE':
            start = entity.dxf.start
            end = entity.dxf.end

            # 바운딩박스 내에 있는지 확인
            mid_x = (start.x + end.x) / 2
            mid_y = (start.y + end.y) / 2
            if not (search_bounds[0] <= mid_x <= search_bounds[2] and
                    search_bounds[1] <= mid_y <= search_bounds[3]):
                continue

            # 폴리곤 내부 또는 근처에 있는지 확인
            mid_point = Point(mid_x, mid_y)
            if not (poly.contains(mid_point) or poly.distance(mid_point) < margin):
                continue

            dx = end.x - start.x
            dy = end.y - start.y
            length = math.sqrt(dx*dx + dy*dy)

            if length > 1:
                angle = math.degrees(math.atan2(dy, dx))
                candidate_lines.append({
                    'angle': angle,
                    'length': length,
                    'is_grainline_layer': is_grainline_layer
                })

    if not candidate_lines:
        return None

    # 그레인라인 레이어에 있는 선 우선
    grainline_layer_lines = [l for l in candidate_lines if l['is_grainline_layer']]
    if grainline_layer_lines:
        best = max(grainline_layer_lines, key=lambda x: x['length'])
        return best['angle']

    return None


def preprocess_dxf_content(file_path):
    """
    DXF 파일 내용을 전처리하여 특수문자 문제를 해결합니다.
    블록명에 <&> 등 ezdxf가 처리할 수 없는 문자가 있으면 치환합니다.
    """
    import tempfile
    import os

    # 파일 읽기 (CP949 우선)
    content = None
    for encoding in ['cp949', 'utf-8', 'latin-1']:
        try:
            with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
                content = f.read()
            break
        except:
            continue

    if content is None:
        return file_path  # 읽기 실패 시 원본 반환

    # 특수문자 치환이 필요한지 확인
    if '<&>' not in content and '<>' not in content:
        return file_path  # 치환 불필요

    # 특수문자 치환 (블록명에서 문제가 되는 문자들)
    content = content.replace('<&>', 'X')  # <&> → X로 치환
    content = content.replace('<>', 'XX')  # <> → XX로 치환

    # 임시 파일로 저장
    temp_dir = tempfile.gettempdir()
    temp_filename = os.path.join(temp_dir, f'dxf_preprocessed_{os.path.basename(file_path)}')
    with open(temp_filename, 'w', encoding='cp949', errors='ignore') as f:
        f.write(content)

    return temp_filename


def scan_dxf_sizes(file_path):
    """
    DXF 파일에서 사이즈 목록만 빠르게 스캔합니다.
    전체 파싱 없이 사이즈 정보만 추출하여 선택 UI에 사용합니다.

    Returns:
        list: 고유한 사이즈 목록 (정렬됨)
    """
    import re

    sizes = set()

    try:
        # 특수문자 전처리
        processed_path = preprocess_dxf_content(file_path)

        # 한글 인코딩(CP949) 우선 시도
        try:
            doc = ezdxf.readfile(processed_path, encoding='cp949')
        except:
            doc = ezdxf.readfile(processed_path)

        msp = doc.modelspace()

        # 방법 1: INSERT 블록에서 사이즈 추출
        for entity in msp:
            if entity.dxftype() == 'INSERT':
                block_name = entity.dxf.name

                # 블록명에서 사이즈 추출 (예: BLK_1_XS, 앞판_M)
                if '_' in block_name:
                    _, potential_size = block_name.rsplit('_', 1)
                    if re.match(r'^([0-9]*X{1,3}L?|[SML]|XS|\d{2,3})$', potential_size, re.IGNORECASE):
                        sizes.add(potential_size.upper())

                # 블록 내 TEXT에서 SIZE: 필드 추출
                try:
                    block = doc.blocks.get(block_name)
                    for be in block:
                        if be.dxftype() == 'TEXT':
                            text = be.dxf.text
                            text_upper = text.upper()
                            if text_upper.startswith('SIZE:'):
                                size_val = text.split(':', 1)[1].strip()
                                if size_val:
                                    sizes.add(size_val.upper())
                except:
                    pass
    except Exception as e:
        st.error(f"사이즈 스캔 오류: {e}")
        return []

    # 사이즈 정렬 (숫자 사이즈 우선, 문자 사이즈 후순)
    def size_sort_key(s):
        # 숫자 사이즈 (85, 90, 95, 100, 105, 110, 115, 120...)
        if s.isdigit():
            return (0, int(s))
        # 문자 사이즈 순서 정의
        size_order = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '2XL', '3XL', '0X', '1X', '2X', '3X']
        if s in size_order:
            return (1, size_order.index(s))
        return (2, s)

    return sorted(list(sizes), key=size_sort_key)


@st.cache_data
def process_dxf(file_path, selected_sizes=None):
    """
    DXF 파일을 읽어 (Polygon, 패턴이름, 원단명, 사이즈) 튜플 리스트를 반환합니다.
    블록(INSERT) 기반으로 처리하여 패턴 누락을 방지합니다.

    Args:
        file_path: DXF 파일 경로
        selected_sizes: 선택된 사이즈 목록 (None이면 전체 로딩)
    """
    import re

    try:
        # 특수문자 전처리 (블록명에 <&> 등이 있으면 치환)
        processed_path = preprocess_dxf_content(file_path)

        # 한글 인코딩(CP949) 우선 시도
        try:
            doc = ezdxf.readfile(processed_path, encoding='cp949')
        except:
            doc = ezdxf.readfile(processed_path)

        msp = doc.modelspace()

        final = []
        detected_base_size = None  # DXF에서 추출한 기준사이즈

        # 원단명 매핑 (CATEGORY 또는 ANNOTATION 값 → 표준 원단명)
        fabric_map = {
            'LINING': '안감',
            'SHELL': '겉감',
            'INTERLINING': '심지',
            'MESH': '메쉬',
            '겉감': '겉감',
            '안감': '안감',
            '심지': '심지',
            '메쉬': '메쉬',
            '니트': '니트',
        }

        # 모델스페이스에서 기준사이즈 먼저 검색 (SAMPLE SIZE, BASE SIZE 등)
        base_size_prefixes = [
            'BASE_SIZE:', 'BASESIZE:', 'BASE SIZE:',
            'REF_SIZE:', 'REFSIZE:', 'REF SIZE:',
            'SAMPLE_SIZE:', 'SAMPLESIZE:', 'SAMPLE SIZE:'
        ]
        for entity in msp:
            if entity.dxftype() == 'TEXT':
                text = entity.dxf.text
                text_upper = text.upper().strip()
                for prefix in base_size_prefixes:
                    if text_upper.startswith(prefix):
                        base_val = text.split(':', 1)[1].strip()
                        if base_val and not detected_base_size:
                            detected_base_size = base_val
                        break
            if detected_base_size:
                break

        # 방법 1: INSERT 블록 기반 추출 (YUKA CAD 등)
        for entity in msp:
            if entity.dxftype() == 'INSERT':
                block_name = entity.dxf.name
                try:
                    block = doc.blocks.get(block_name)
                    max_poly = None
                    max_area = 0
                    pattern_name = ""
                    fabric_name = ""  # 원단명 추출용
                    size_name = ""    # 사이즈 추출용
                    pattern_group = ""  # 패턴 그룹 번호 (블록명에서 추출)
                    piece_name = ""   # PIECE NAME 필드
                    dxf_quantity = 0  # QUANTITY 필드 (원본 수량)

                    # 블록명에서 패턴그룹/사이즈 추출
                    # 형식1: BLK_1_XS → 그룹:1, 사이즈:XS
                    # 형식2: 앞판-a_M → 그룹:앞판-a, 사이즈:M
                    # 마지막 _를 기준으로 분리하여 사이즈 패턴 확인
                    if '_' in block_name:
                        base_name, potential_size = block_name.rsplit('_', 1)
                        # 사이즈 패턴: S, M, L, XS, XL, XXL, 2XL, 3XL, 0X, 1X, 00X, 85, 90 등
                        if re.match(r'^([0-9]*X{1,2}L?|[SML]|XS|\d{2,3})$', potential_size, re.IGNORECASE):
                            size_name = potential_size
                            pattern_group = base_name  # 사이즈 앞부분 전체를 그룹으로 사용
                        else:
                            # 사이즈 패턴이 아니면 기존 방식 시도 (BLK_1_0X 형식)
                            block_parts = block_name.split('_')
                            if len(block_parts) >= 2 and block_parts[1].isdigit():
                                pattern_group = block_parts[1]

                    # 블록 내 가장 큰 닫힌 POLYLINE 선택 + 텍스트 추출
                    for be in block:
                        if be.dxftype() == 'POLYLINE' and be.is_closed:
                            pts = list(be.points())
                            if len(pts) >= 3:
                                coords = [(p[0], p[1]) for p in pts]
                                poly = Polygon(coords)
                                # 유효하지 않은 폴리곤은 buffer(0)으로 수정 시도
                                if not poly.is_valid:
                                    poly = poly.buffer(0)
                                if poly.is_valid and poly.area > max_area:
                                    max_area = poly.area
                                    max_poly = poly
                        elif be.dxftype() == 'LWPOLYLINE' and be.closed:
                            pts = list(be.points())
                            if len(pts) >= 3:
                                coords = [(p[0], p[1]) for p in pts]
                                poly = Polygon(coords)
                                # 유효하지 않은 폴리곤은 buffer(0)으로 수정 시도
                                if not poly.is_valid:
                                    poly = poly.buffer(0)
                                if poly.is_valid and poly.area > max_area:
                                    max_area = poly.area
                                    max_poly = poly
                        elif be.dxftype() == 'TEXT':
                            text = be.dxf.text

                            # PIECE NAME 필드에서 패턴 번호/이름 추출 (대소문자 무시)
                            text_upper = text.upper()
                            if text_upper.startswith('PIECE NAME:'):
                                piece_val = text.split(':', 1)[1].strip()
                                if piece_val:
                                    piece_name = piece_val

                            # QUANTITY 필드에서 원본 수량 추출 (대소문자 무시)
                            elif text_upper.startswith('QUANTITY:') or text_upper.startswith('QTY:'):
                                qty_val = text.split(':', 1)[1].strip()
                                if qty_val and qty_val.isdigit():
                                    dxf_quantity = int(qty_val)

                            # SIZE 필드에서 사이즈 추출 (대소문자 무시)
                            elif text_upper.startswith('SIZE:'):
                                size_val = text.split(':', 1)[1].strip()
                                if size_val:
                                    size_name = size_val

                            # CATEGORY 필드에서 원단명 추출 (대소문자 무시)
                            elif text_upper.startswith('CATEGORY:'):
                                cat_val = text.split(':', 1)[1].strip()
                                if cat_val:
                                    # 매핑된 원단명 찾기
                                    for key, mapped in fabric_map.items():
                                        if key.upper() == cat_val.upper() or key == cat_val:
                                            fabric_name = mapped
                                            break
                                    # 매핑 안 되면 원본 사용
                                    if not fabric_name and cat_val:
                                        fabric_name = cat_val

                            # ANNOTATION 필드 처리 (대소문자 무시)
                            elif text_upper.startswith('ANNOTATION:'):
                                val = text.split(':', 1)[1].strip()
                                if not val:
                                    continue

                                # ANNOTATION에서 원단명 키워드 체크 (LINING 등)
                                val_upper = val.upper()
                                if val_upper in fabric_map:
                                    if not fabric_name:  # CATEGORY가 없을 때만
                                        fabric_name = fabric_map[val_upper]
                                    continue

                                # 사이즈 호칭 추출: <S>, <M>, <L>, <0X> 등
                                if val.startswith('<') and val.endswith('>'):
                                    extracted_size = val[1:-1].strip()
                                    if extracted_size and not size_name:
                                        size_name = extracted_size
                                    continue

                                # 제외 대상 체크
                                # 스타일 번호: S/#..., M/#... 등
                                if val.startswith(('S/', 'M/', 'L/', '#')):
                                    continue
                                # 숫자만 (사이즈: 130, 80 등)
                                if val.isdigit():
                                    continue
                                # 숫자로 시작 (스타일명: 35717요척 등)
                                if val[0].isdigit():
                                    continue
                                # 원단명 (이미 위에서 처리됨)
                                fabric_keywords = ['LINING', 'SHELL', 'INTERLINING', '안감', '겉감', '심지']
                                if val.upper() in [f.upper() for f in fabric_keywords]:
                                    continue
                                # 배색 관련
                                if '배색' in val:
                                    continue
                                # 문장 제외 (공백 2개 이상 또는 길이 5자 초과)
                                if val.count(' ') >= 2 or len(val) > 5:
                                    continue
                                # 괄호가 있는 설명문 제외
                                if '(' in val or ')' in val:
                                    continue
                                # 한글 부위명 우선 (한글이 포함되면 우선 선택)
                                has_korean = any('\uac00' <= c <= '\ud7a3' for c in val)
                                if has_korean:
                                    pattern_name = val  # 한글 부위명 덮어쓰기
                                elif not pattern_name:
                                    pattern_name = val  # 영문 부위명 (한글 없을 때만)

                    # 원단명 기본값: 겉감
                    if not fabric_name:
                        fabric_name = "겉감"

                    # 패턴 이름이 없으면 PIECE NAME 번호 사용
                    if not pattern_name and piece_name:
                        pattern_name = piece_name

                    # pattern_group이 숫자만으로 구성된 경우 piece_name을 그룹으로 사용
                    # 예: 블록명 "1", "2", "3"... 인 경우 piece_name "BK1", "FRT2"를 그룹으로 사용
                    if piece_name and (not pattern_group or pattern_group.isdigit()):
                        pattern_group = piece_name

                    # 10cm² 이상인 패턴만 추가 (작은 부속 패턴 포함)
                    if max_poly and (max_area / 100) >= 10:
                        # 선택된 사이즈 필터링 (selected_sizes가 지정된 경우)
                        if selected_sizes is not None:
                            # 사이즈명을 대문자로 비교
                            size_upper = size_name.upper() if size_name else ""
                            selected_upper = [s.upper() for s in selected_sizes]
                            if size_upper not in selected_upper:
                                continue  # 선택되지 않은 사이즈는 건너뛰기

                        # 그레인라인 감지 및 패턴 회전 (수직 정렬)
                        grainline_info = None  # (start, end) 좌표
                        grainline_angle, gl_start, gl_end = detect_grainline(block, return_coords=True)
                        if grainline_angle is not None and gl_start and gl_end:
                            # 패턴 회전
                            centroid = (max_poly.centroid.x, max_poly.centroid.y)
                            max_poly, rotation_applied = rotate_polygon_to_vertical_grain(max_poly, grainline_angle)
                            # 그레인라인 좌표도 함께 회전
                            gl_start, gl_end = rotate_grainline_coords(gl_start, gl_end, rotation_applied, centroid)
                            grainline_info = (gl_start, gl_end)

                        # 튜플: (poly, pattern_name, fabric_name, size_name, pattern_group, piece_name, dxf_quantity, grainline_info)
                        final.append((max_poly, pattern_name, fabric_name, size_name, pattern_group, piece_name, dxf_quantity, grainline_info))
                except:
                    pass

        # 방법 2: INSERT가 없으면 기존 방식 (레거시 DXF 호환)
        if not final:
            lines = []
            for e in msp:
                extract_lines(e, lines)

            rounded_lines = []
            for line in lines:
                coords = list(line.coords)
                rounded_coords = [(round(x, 1), round(y, 1)) for x, y in coords]
                rounded_lines.append(LineString(rounded_coords))

            merged_lines = linemerge(rounded_lines)
            raw_polys = list(polygonize(merged_lines))

            # 열린 선분 강제 닫기
            if hasattr(merged_lines, 'geoms'):
                chains = list(merged_lines.geoms)
            else:
                chains = [merged_lines] if merged_lines else []

            for chain in chains:
                if not chain.is_ring:
                    try:
                        start_pt = Point(chain.coords[0])
                        end_pt = Point(chain.coords[-1])
                        gap = start_pt.distance(end_pt)
                        if gap < 10.0:
                            closed_coords = list(chain.coords) + [chain.coords[0]]
                            new_poly = Polygon(closed_coords)
                            if new_poly.is_valid and new_poly.area > 0:
                                raw_polys.append(new_poly)
                    except:
                        pass

            candidates = [p for p in raw_polys if (p.area / 100) >= 10]
            candidates.sort(key=lambda x: x.area, reverse=True)

            # 레거시 방식에서만 중복 제거 (패턴 이름/원단명/사이즈/그룹 없음 → 기본값)
            added_polys = []
            for idx, p in enumerate(candidates):
                if not any(p.centroid.distance(e.centroid) < 50 for e in added_polys):
                    # 그레인라인 감지 및 패턴 회전 (수직 정렬) - 레거시 방식
                    grainline_angle = detect_grainline_for_polygon(msp, p)
                    grainline_info = None
                    if grainline_angle is not None:
                        centroid = (p.centroid.x, p.centroid.y)
                        p, _ = rotate_polygon_to_vertical_grain(p, grainline_angle)
                    added_polys.append(p)
                    # 튜플: (poly, pattern_name, fabric_name, size_name, pattern_group, piece_name, dxf_qty, grainline_info)
                    final.append((p, "", "겉감", "", str(idx + 1), "", 0, grainline_info))

        # 면적 기준 정렬 (큰 것부터)
        final.sort(key=lambda x: x[0].area, reverse=True)
        return final, detected_base_size

    except Exception as e:
        return [], None


def sort_by_fabric():
    """
    세션 상태의 patterns와 df를 원단 우선으로 정렬합니다.
    패턴 수정(복사, 삭제, 원단 변경 등) 후 호출하여 일관된 정렬을 유지합니다.
    """
    if 'df' not in st.session_state or st.session_state.df.empty:
        return

    df = st.session_state.df
    patterns = st.session_state.patterns

    # 원단, 번호 순으로 정렬
    sort_indices = df.sort_values(by=['원단', '번호']).index.tolist()
    st.session_state.patterns = [patterns[i] for i in sort_indices]
    st.session_state.df = df.iloc[sort_indices].reset_index(drop=True)
    st.session_state.df["번호"] = range(1, len(st.session_state.df) + 1)

    # 체크박스 상태 초기화
    for i in range(len(st.session_state.patterns)):
        st.session_state[f"chk_{i}"] = False


def update_nesting_pattern_names():
    """
    네스팅 결과의 패턴 이름을 상세리스트의 구분명으로 업데이트합니다.
    상세리스트에서 구분 수정 시 호출하여 마카 표시에 반영합니다.
    pattern_id 형식: "df인덱스:이름\n사이즈_수량인덱스"
    """
    if 'nesting_results' not in st.session_state or not st.session_state.nesting_results:
        return
    if 'df' not in st.session_state or st.session_state.df.empty:
        return

    df = st.session_state.df

    # 네스팅 결과의 pattern_id 업데이트
    for fabric, result in st.session_state.nesting_results.items():
        if 'placements' not in result:
            continue
        for placement in result['placements']:
            old_id = placement['pattern_id']
            # pattern_id 형식: "df인덱스:이름\n사이즈_수량인덱스"
            # 예: "5:등판\nL_0" → df인덱스=5, 사이즈=L

            # 수량 인덱스 분리 (마지막 _숫자)
            if '_' in old_id:
                base_part, qty_idx = old_id.rsplit('_', 1)
            else:
                base_part, qty_idx = old_id, ""

            # 사이즈 분리
            if '\n' in base_part:
                idx_name_part, size_part = base_part.split('\n', 1)
            else:
                idx_name_part, size_part = base_part, ""

            # df 인덱스 추출
            if ':' in idx_name_part:
                df_idx_str = idx_name_part.split(':')[0]
            else:
                df_idx_str = idx_name_part

            # df에서 새 이름 가져오기
            try:
                df_idx = int(df_idx_str)
                if df_idx < len(df):
                    new_name = str(df.at[df_idx, '구분'])[:8] if df.at[df_idx, '구분'] else ""
                else:
                    new_name = ""
            except (ValueError, KeyError):
                new_name = ""

            # 새 pattern_id 생성
            if size_part:
                new_base = f"{df_idx_str}:{new_name}\n{size_part}"
            else:
                new_base = f"{df_idx_str}:{new_name}"

            if qty_idx:
                placement['pattern_id'] = f"{new_base}_{qty_idx}"
            else:
                placement['pattern_id'] = new_base


# ==============================================================================
# 4. UI 컴포넌트: 팝업 뷰어 (Dialog)
# ==============================================================================

@st.dialog("🔍 패턴 정밀 검토", width="large")
def show_detail_viewer(idx, pattern, fabric_name):
    """상세 보기 팝업창을 띄웁니다. (확대/이동/회전 기능 포함)"""
    st.caption("💡 사용법: **마우스 휠**로 줌, **드래그**로 이동, **슬라이더**로 회전")
    
    # 회전 컨트롤
    angle = st.slider("회전 각도 조절", 0, 360, 0, 90, label_visibility="collapsed")
    rotated_poly = affinity.rotate(pattern, angle, origin='centroid')
    
    # Plotly 데이터 준비
    x, y = rotated_poly.exterior.xy
    fill_color = get_fabric_color_hex(fabric_name)
    
    # 차트 그리기
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(x), y=list(y),
        fill="toself", fillcolor=fill_color,
        line=dict(color="black", width=2), mode='lines',
        name=f"Pattern {idx+1}"
    ))
    
    # 차트 레이아웃 설정 (CAD 스타일)
    fig.update_layout(
        xaxis=dict(visible=False), 
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1),
        plot_bgcolor='white', 
        margin=dict(l=10, r=10, t=10, b=10),
        height=600, 
        dragmode='pan' # 기본 도구를 '손바닥(이동)'으로 설정
    )
    st.plotly_chart(fig, width='stretch')
    
    # 하단 정보 표시
    minx, miny, maxx, maxy = rotated_poly.bounds
    w, h = (maxx - minx) / 10, (maxy - miny) / 10
    st.markdown(f"**📏 규격:** 가로 {w:.1f} cm x 세로 {h:.1f} cm")


# ==============================================================================
# 5. 메인 실행부 (Main Execution)
# ==============================================================================

# 세션 상태 초기화
if "df" not in st.session_state: st.session_state.df = None
if "patterns" not in st.session_state: st.session_state.patterns = None
if "loaded_file" not in st.session_state: st.session_state.loaded_file = None
if "dxf_sizes" not in st.session_state: st.session_state.dxf_sizes = None  # 스캔된 사이즈 목록
if "size_selection_done" not in st.session_state: st.session_state.size_selection_done = False  # 사이즈 선택 완료 여부
if "selected_load_sizes" not in st.session_state: st.session_state.selected_load_sizes = None  # 로딩할 사이즈 목록

# A. 파일 업로드 섹션
uploaded_file = st.file_uploader(
    "📁 DXF 파일을 여기에 드래그하거나 클릭하여 선택하세요",
    type=["dxf"],
    help="지원 형식: YUKA, Optitex, Gerber 등"
)

if uploaded_file is not None:
    # 파일이 변경되었으면 캐시 초기화 (파일명 + 크기 체크)
    file_key = f"{uploaded_file.name}_{uploaded_file.size}"
    if st.session_state.loaded_file != file_key:
        st.session_state.patterns = None
        st.session_state.df = None
        st.session_state.loaded_file = file_key
        st.session_state.dxf_sizes = None  # 사이즈 목록 초기화
        st.session_state.size_selection_done = False  # 사이즈 선택 상태 초기화
        st.session_state.selected_load_sizes = None  # 선택 사이즈 초기화
        # 체크박스 상태도 초기화
        for key in list(st.session_state.keys()):
            if key.startswith("chk_") or key.startswith("size_chk_"):
                del st.session_state[key]

    # 임시 파일 생성 (사이즈 스캔 및 패턴 로딩에 공용 사용)
    tmp_path = None
    if st.session_state.dxf_sizes is None or st.session_state.patterns is None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_path = tmp_file.name

    # 1단계: 사이즈 스캔 (파일 업로드 직후)
    if st.session_state.dxf_sizes is None and tmp_path:
        with st.spinner("사이즈 목록 스캔 중..."):
            scanned_sizes = scan_dxf_sizes(tmp_path)
            st.session_state.dxf_sizes = scanned_sizes
            # 사이즈가 1개 이하면 바로 선택 완료 처리 (선택 UI 불필요)
            if len(scanned_sizes) <= 1:
                st.session_state.size_selection_done = True
                st.session_state.selected_load_sizes = scanned_sizes if scanned_sizes else None

    # 2단계: 사이즈 선택 UI (여러 사이즈가 있을 때)
    if st.session_state.dxf_sizes and len(st.session_state.dxf_sizes) > 1 and not st.session_state.size_selection_done:
        st.info(f"📏 **{len(st.session_state.dxf_sizes)}개 사이즈 발견** - 불러올 사이즈를 선택하세요")

        # 전체 선택/해제 버튼
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("✅ 전체 선택", use_container_width=True):
                for size in st.session_state.dxf_sizes:
                    st.session_state[f"size_chk_{size}"] = True
                st.rerun()
        with col2:
            if st.button("⬜ 전체 해제", use_container_width=True):
                for size in st.session_state.dxf_sizes:
                    st.session_state[f"size_chk_{size}"] = False
                st.rerun()

        # 사이즈 체크박스 (가로 배열)
        cols = st.columns(min(6, len(st.session_state.dxf_sizes)))
        for i, size in enumerate(st.session_state.dxf_sizes):
            col_idx = i % len(cols)
            with cols[col_idx]:
                # 기본값: 전체 선택
                default_val = st.session_state.get(f"size_chk_{size}", True)
                st.checkbox(size, value=default_val, key=f"size_chk_{size}")

        # 선택 완료 버튼
        selected_count = sum(1 for size in st.session_state.dxf_sizes if st.session_state.get(f"size_chk_{size}", True))
        st.write(f"선택된 사이즈: **{selected_count}개** / 전체 {len(st.session_state.dxf_sizes)}개")

        if st.button(f"🚀 선택한 {selected_count}개 사이즈 불러오기", type="primary", use_container_width=True, disabled=(selected_count == 0)):
            # 선택된 사이즈 목록 저장
            selected = [size for size in st.session_state.dxf_sizes if st.session_state.get(f"size_chk_{size}", True)]
            st.session_state.selected_load_sizes = selected if selected else None
            st.session_state.size_selection_done = True
            st.rerun()

        # 임시 파일 삭제 (사이즈 선택 전에는 패턴 로딩 안 함)
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        st.stop()  # 사이즈 선택 완료 전까지 아래 코드 실행 안 함

    # 3단계: 선택된 사이즈만 패턴 로딩
    if st.session_state.patterns is None and st.session_state.size_selection_done:
        if tmp_path is None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name

        with st.spinner("패턴 로딩 중..."):
            # 캐시 키용으로 리스트를 튜플로 변환
            sizes_tuple = tuple(st.session_state.selected_load_sizes) if st.session_state.selected_load_sizes else None
            patterns, detected_base_size = process_dxf(tmp_path, sizes_tuple)
            style_no = extract_style_no(tmp_path)
        os.remove(tmp_path)  # 임시 파일 삭제
        st.session_state.patterns = patterns
        st.session_state.detected_base_size = detected_base_size  # DXF에서 추출한 기준사이즈
        st.session_state.style_no = style_no
        st.session_state.original_pattern_count = len(patterns)  # 원본 패턴 수 저장
        st.session_state.thumbnail_cache = {}  # 썸네일 캐시 초기화
        
        # 패턴 DB 로드 (수량 추천용)
        pattern_db = None
        if PATTERN_DB_AVAILABLE:
            try:
                pattern_db = PatternDB()
            except:
                pass

        # 사이즈 목록 추출 (작은 사이즈 → 큰 사이즈 순)
        def size_sort_key(size):
            """사이즈를 작은 것부터 큰 것 순으로 정렬하기 위한 키"""
            size_upper = size.upper()
            # 표준 의류 사이즈 순서
            standard_order = {
                'XS': 10, 'S': 20, 'M': 30, 'L': 40,
                'XL': 50, '2XL': 60, 'XXL': 60,
                '3XL': 70, 'XXXL': 70,
                '4XL': 80, '5XL': 90, '6XL': 100
            }
            if size_upper in standard_order:
                return standard_order[size_upper]
            # 숫자 사이즈 (85, 90, 95, 100, 105 등)
            if size.isdigit():
                return int(size)
            # 0X, 2X, 4X 등 (아동/특수 사이즈)
            if size_upper.endswith('X') and size_upper[:-1].isdigit():
                return int(size_upper[:-1])
            # 기타: 알파벳 순
            return 500 + ord(size_upper[0]) if size_upper else 999

        all_sizes = sorted(set(p[3] for p in patterns if p[3]), key=size_sort_key)
        st.session_state.all_sizes = all_sizes
        st.session_state.selected_sizes = all_sizes.copy() if all_sizes else []

        # 기준사이즈 결정
        # 1순위: DXF에서 추출한 기준사이즈 (BASE_SIZE: 또는 REF_SIZE:)
        # 2순위: 없으면 가운데 사이즈 사용
        if detected_base_size and detected_base_size in all_sizes:
            st.session_state.base_size = detected_base_size
        elif all_sizes:
            mid_idx = len(all_sizes) // 2
            st.session_state.base_size = all_sizes[mid_idx]
        else:
            st.session_state.base_size = None

        # 누락된 사이즈 패턴 자동 채우기
        # pattern_group별로 어떤 사이즈가 있는지 확인하고, 기준사이즈 패턴으로 채움
        if all_sizes and st.session_state.base_size:
            base_size = st.session_state.base_size

            # pattern_group별 사이즈 매핑
            group_size_map = {}  # {(pattern_group, fabric): {size: pattern_idx, ...}}
            for idx, p_data in enumerate(patterns):
                # 튜플: (poly, pattern_name, fabric_name, size_name, pattern_group, piece_name, dxf_quantity)
                pattern_group = p_data[4]
                size_name = p_data[3]
                fabric_name = p_data[2]
                if pattern_group and size_name:
                    key = (pattern_group, fabric_name if fabric_name else "겉감")
                    if key not in group_size_map:
                        group_size_map[key] = {}
                    group_size_map[key][size_name] = idx

            # 누락된 사이즈 찾아서 기준사이즈 패턴으로 채우기
            added_patterns = []
            missing_info = []
            for (pattern_group, fabric), size_dict in group_size_map.items():
                if base_size in size_dict:
                    # 기준사이즈 패턴이 있는 경우만 처리
                    base_idx = size_dict[base_size]
                    base_pattern = patterns[base_idx]

                    for size in all_sizes:
                        if size not in size_dict:
                            # 누락된 사이즈 - 기준사이즈 패턴 복사 (8개 요소)
                            new_pattern = (
                                base_pattern[0],  # poly (기준사이즈 형상 사용)
                                base_pattern[1],  # pattern_name
                                base_pattern[2],  # fabric_name
                                size,             # 새 사이즈
                                pattern_group,    # pattern_group
                                base_pattern[5] if len(base_pattern) > 5 else "",  # piece_name
                                base_pattern[6] if len(base_pattern) > 6 else 0,   # dxf_quantity
                                base_pattern[7] if len(base_pattern) > 7 else None # grainline_info
                            )
                            added_patterns.append(new_pattern)
                            missing_info.append(f"{pattern_group}_{size}")

            if added_patterns:
                patterns.extend(added_patterns)
                st.session_state.patterns = patterns
                st.toast(f"⚠️ 누락 사이즈 {len(added_patterns)}개 자동 추가: {', '.join(missing_info[:5])}{'...' if len(missing_info) > 5 else ''}")

        # 초기 데이터프레임 생성 (같은 패턴은 사이즈별 동일 번호)
        # 1단계: 패턴 정보 수집 및 번호 매핑 생성
        pattern_info = []
        pattern_number_map = {}  # (pattern_name, fabric) -> 번호
        current_number = 0

        for i, p_data in enumerate(patterns):
            # 튜플: (poly, pattern_name, fabric_name, size_name, pattern_group, piece_name, dxf_quantity)
            poly = p_data[0]
            pattern_name = p_data[1]
            fabric_name = p_data[2]
            size_name = p_data[3]
            pattern_group = p_data[4]
            piece_name = p_data[5] if len(p_data) > 5 else ""
            dxf_quantity = p_data[6] if len(p_data) > 6 else 0

            minx, miny, maxx, maxy = poly.bounds
            w, h = (maxx - minx) / 10, (maxy - miny) / 10
            extracted_fabric = fabric_name if fabric_name else "겉감"

            # 수량/구분 결정 우선순위:
            # 1순위: DXF 원본 정보 (QUANTITY, ANNOTATION 부위명)
            # 2순위: 패턴 DB 예측
            # 3순위: 형상 기반 추론

            # 1순위: DXF 원본 수량 (QUANTITY 필드)
            if dxf_quantity > 0:
                count = dxf_quantity
                default_desc = pattern_name if pattern_name else "확인"
                db_used = True  # 원본 사용 표시
            else:
                # 2순위: 패턴 DB 예측
                db_used = False
                if pattern_db and len(pattern_db.records) > 0:
                    pred_qty, pred_cat, confidence, refs = pattern_db.predict_quantity(poly)
                    if confidence >= 0.5:
                        count = pred_qty
                        default_desc = pred_cat
                        db_used = True

                # 3순위: 형상 기반 추론
                if not db_used:
                    is_symmetric, sym_reason = check_symmetry(poly)

                    # 1. 좌우대칭 + 가로≥50cm + 세로≥45cm → BACK, 1개
                    if sym_reason == "좌우대칭" and w >= 50 and h >= 45:
                        count = 1
                        default_desc = "BACK"
                    # 2. 좌우대칭 + 가로≥50cm + 세로≥20cm + 세로<45cm → BACK YOKE, 1개
                    elif sym_reason == "좌우대칭" and w >= 50 and h >= 20 and h < 45:
                        count = 1
                        default_desc = "BACK YOKE"
                    # 3. 가로≥25cm + 세로≥40cm + 세로직선(≥35cm) 1개 이상 → FRONT, 2개
                    elif w >= 25 and h >= 40 and check_vertical_straight_edge(poly, 35):
                        count = 2
                        default_desc = "FRONT"
                    # 3. 좌우대칭 + 가로≥45cm + 세로≤15cm + 가로직선 1개 → BACK YOKE HEM, 1개
                    elif sym_reason == "좌우대칭" and w >= 45 and h <= 15 and check_horizontal_straight_edge(poly):
                        count = 1
                        default_desc = "BACK YOKE HEM"
                    # 5. 좌우대칭 + 가로≥50cm + 세로≤10cm + 평행선(85%) → BACK BOTTOM, 1개
                    elif sym_reason == "좌우대칭" and w >= 50 and h <= 10 and check_parallel_edges(poly, 0.85):
                        count = 1
                        default_desc = "BACK BOTTOM"
                    # 6. 좌우대칭 + 가로≤25cm + 세로≤15cm → FLAP, 4개
                    elif sym_reason == "좌우대칭" and w <= 25 and h <= 15:
                        count = 4
                        default_desc = "FLAP"
                    # 7. 상하대칭 + 가로≤26cm + 세로≤12cm → SLEEVE TAB, 4개
                    elif sym_reason == "상하대칭" and w <= 26 and h <= 12:
                        count = 4
                        default_desc = "SLEEVE TAB"
                    # 8. 나머지 → 확인, 2개
                    else:
                        count = 2
                        default_desc = "확인"

            # 구분(패턴 이름) 결정: DXF 원본 부위명 우선
            if pattern_name:
                desc = pattern_name
            elif db_used and dxf_quantity == 0:
                desc = default_desc
            else:
                desc = default_desc

            # 패턴 키: pattern_group + fabric으로 동일 패턴 식별
            # pattern_group은 DXF 블록명에서 추출된 패턴 번호 (예: "1", "2", "3")
            if pattern_group:
                pattern_key = (pattern_group, extracted_fabric)
            else:
                # pattern_group이 없으면 pattern_name 사용
                pattern_key = (pattern_name or f"P{i}", extracted_fabric)
            if pattern_key not in pattern_number_map:
                current_number += 1
                pattern_number_map[pattern_key] = current_number

            pattern_info.append({
                'poly': poly, 'pattern_name': pattern_name, 'extracted_fabric': extracted_fabric,
                'size_name': size_name, 'w': w, 'h': h, 'count': count, 'desc': desc,
                'pattern_key': pattern_key
            })

        # 2단계: 데이터프레임 생성
        data_list = []
        for info in pattern_info:
            pattern_num = pattern_number_map[info['pattern_key']]
            data_list.append({
                "형상": poly_to_base64(info['poly'], get_fabric_color_hex(info['extracted_fabric'])),
                "번호": pattern_num, "사이즈": info['size_name'], "원단": info['extracted_fabric'],
                "구분": info['desc'], "수량": info['count'],
                "가로(cm)": round(info['w'], 1), "세로(cm)": round(info['h'], 1),
                "면적_raw": info['poly'].area / 1000000
            })
        st.session_state.df = pd.DataFrame(data_list)
        # 원단별 정렬 (기본 정렬) - patterns 리스트도 동기화
        if not st.session_state.df.empty:
            sort_indices = st.session_state.df.sort_values(by=['원단', '번호']).index.tolist()
            st.session_state.patterns = [st.session_state.patterns[i] for i in sort_indices]
            st.session_state.df = st.session_state.df.iloc[sort_indices].reset_index(drop=True)
            st.session_state.df["번호"] = range(1, len(st.session_state.df) + 1)  # 번호 순차 재설정
        # 체크박스 상태 초기화
        for i in range(len(patterns)): st.session_state[f"chk_{i}"] = False

    # 데이터 로드
    patterns = st.session_state.patterns
    df = st.session_state.df

    if patterns:
        # 썸네일 비율 고정용 Max값 계산 (현재 남아있는 패턴 기준)
        # 패턴 삭제 시 가장 큰 패턴 기준으로 자동 재설정됨
        max_dim = 0
        for p_data in patterns:
            p = p_data[0]
            minx, miny, maxx, maxy = p.bounds
            max_dim = max(max_dim, maxx - minx, maxy - miny)
        zoom_span = max_dim * 1.1 if max_dim > 0 else 100  # 기본값 설정

        # ----------------------------------------------------------------
        # A. 사이즈 선택 UI (그레이딩된 DXF용)
        # ----------------------------------------------------------------
        all_sizes = st.session_state.get('all_sizes', [])
        if len(all_sizes) >= 1:
            # 기준사이즈 정보 표시
            base_size = st.session_state.get('base_size')
            detected_base = st.session_state.get('detected_base_size')
            source_info = "(원본)" if detected_base and detected_base == base_size else "(중간)"

            if len(all_sizes) == 1:
                # 사이즈가 1개일 때: 간단한 정보만 표시
                st.markdown(f"#### 📐 사이즈: **{all_sizes[0]}** {source_info}")
                st.session_state.selected_sizes = all_sizes.copy()
            else:
                # 사이즈가 2개 이상일 때: 선택 UI 표시
                st.markdown(f"#### 📐 사이즈 선택 (기준: **{base_size}** {source_info})")

                # 사이즈 선택 체크박스
                size_cols = st.columns(min(len(all_sizes) + 2, 10))

                # 전체 선택/해제 버튼
                with size_cols[0]:
                    if st.button("✅전체", key="size_all", width='stretch'):
                        st.session_state.selected_sizes = all_sizes.copy()
                        st.rerun()
                with size_cols[1]:
                    if st.button("⬜해제", key="size_none", width='stretch'):
                        st.session_state.selected_sizes = []
                        st.rerun()

                # 개별 사이즈 체크박스
                selected_sizes = st.session_state.get('selected_sizes', all_sizes)
                new_selected = []
                for idx, size in enumerate(all_sizes):
                    with size_cols[idx + 2] if idx + 2 < len(size_cols) else size_cols[-1]:
                        if st.checkbox(size, value=(size in selected_sizes), key=f"sz_{size}"):
                            new_selected.append(size)

                if new_selected != selected_sizes:
                    st.session_state.selected_sizes = new_selected
                    st.rerun()

                st.markdown(f"**선택된 사이즈:** {', '.join(st.session_state.selected_sizes) if st.session_state.selected_sizes else '없음'}")

            # 중첩 시각화
            with st.expander("🔍 사이즈 중첩 비교", expanded=True):
                # 패턴 그룹별로 분류 (상세 리스트 번호 기준) - 복사 패턴 제외
                from collections import defaultdict
                original_count = st.session_state.get('original_pattern_count', len(patterns))
                pattern_groups = defaultdict(list)

                # 기준 사이즈 패턴의 순차 번호 매핑 (pattern_group -> list_idx)
                base_size = st.session_state.get('base_size')
                group_to_list_idx = {}
                list_idx = 0
                for idx, p_data in enumerate(patterns):
                    if idx >= original_count:
                        continue
                    size_name = p_data[3]
                    pattern_group = p_data[4]
                    # 기준 사이즈이거나 사이즈 없는 패턴만 번호 부여
                    if not all_sizes or size_name == base_size or not size_name:
                        list_idx += 1
                        if pattern_group:
                            group_to_list_idx[pattern_group] = list_idx

                for idx, p_data in enumerate(patterns):
                    # 복사된 패턴은 제외 (원본 패턴 수 이후 인덱스)
                    if idx >= original_count:
                        continue

                    # 튜플: (poly, pattern_name, fabric_name, size_name, pattern_group, ...)
                    size_name = p_data[3]
                    pattern_group = p_data[4]
                    pattern_name = p_data[1]
                    if size_name:  # 사이즈가 있는 패턴만
                        # 상세 리스트의 순차 번호 사용 (일괄 수정 도구와 동일)
                        if pattern_group and pattern_group in group_to_list_idx:
                            group_key = f"{group_to_list_idx[pattern_group]}"
                        elif pattern_group:
                            group_key = f"{pattern_group}"
                        elif pattern_name:
                            group_key = pattern_name
                        else:
                            group_key = "기타"
                        pattern_groups[group_key].append(p_data)

                if pattern_groups:
                    # 전역 최대 크기 계산 (모든 패턴에 동일 비율 적용)
                    global_max_dim = 0
                    for group_patterns in pattern_groups.values():
                        for p_data in group_patterns:
                            poly = p_data[0]
                            minx, miny, maxx, maxy = poly.bounds
                            max_dim = max(maxx - minx, maxy - miny)
                            if max_dim > global_max_dim:
                                global_max_dim = max_dim

                    # 그룹 수에 따라 컬럼 조정 (최대 6열)
                    num_groups = len(pattern_groups)
                    num_cols = min(num_groups, 6)

                    # 그룹을 숫자 기준으로 정렬 (1, 2, 3... 순서로)
                    def sort_key(item):
                        name = item[0]
                        # 숫자로만 구성된 경우 숫자 정렬
                        if name.isdigit():
                            return (0, int(name))
                        # "패턴 N" 형식 (fallback)
                        if name.startswith("패턴 "):
                            try:
                                return (0, int(name.replace("패턴 ", "")))
                            except:
                                return (1, name)
                        return (2, name)

                    sorted_groups = sorted(pattern_groups.items(), key=sort_key)

                    # 행별로 그룹 표시
                    for row_start in range(0, len(sorted_groups), num_cols):
                        row_groups = sorted_groups[row_start:row_start + num_cols]
                        overlay_cols = st.columns(len(row_groups))

                        for col_idx, (group_name, group_patterns) in enumerate(row_groups):
                            with overlay_cols[col_idx]:
                                st.caption(f"**{group_name}번** ({len(group_patterns)}개)")
                                fig = create_overlay_visualization(
                                    group_patterns,
                                    st.session_state.selected_sizes,
                                    all_sizes,
                                    global_max_dim
                                )
                                st.pyplot(fig, width='stretch')
                                plt.close(fig)
                else:
                    st.info("사이즈 정보가 있는 패턴이 없습니다.")

            st.divider()

        # ----------------------------------------------------------------
        # B. 일괄 수정 도구 (Batch Edit Tools)
        # ----------------------------------------------------------------
        st.markdown("#### ✨ 일괄 수정 도구")
        tool_col1, tool_col2, tool_col3 = st.columns([1.5, 1.5, 2])

        # 패턴 그룹 매핑 (동일 패턴의 다른 사이즈 연결)
        all_sizes = st.session_state.get('all_sizes', [])
        selected_sizes = st.session_state.get('selected_sizes', all_sizes)
        # 기준사이즈: DXF에서 추출한 값 또는 가운데 사이즈
        base_size = st.session_state.get('base_size', selected_sizes[0] if selected_sizes else None)

        group_to_indices = {}
        base_indices_set = set()
        for idx, p_data in enumerate(patterns):
            size_name = p_data[3]
            pattern_group = p_data[4]
            if pattern_group:
                if pattern_group not in group_to_indices:
                    group_to_indices[pattern_group] = {}
                group_to_indices[pattern_group][size_name] = idx
            # 기본 사이즈 인덱스
            if not all_sizes or size_name == base_size or not size_name:
                base_indices_set.add(idx)

        # 1. 전체 선택/해제/복사/삭제
        with tool_col1:
            c1, c2, c3, c4 = st.columns(4)
            if c1.button("✅전체", width='stretch', help="기본 사이즈 전체 선택"):
                for i in base_indices_set: st.session_state[f"chk_{i}"] = True
                st.rerun()
            if c2.button("⬜해제", width='stretch', help="모든 선택 해제"):
                for i in range(len(patterns)): st.session_state[f"chk_{i}"] = False
                st.rerun()
            if c3.button("📋복사", width='stretch', help="선택 패턴 복사"):
                sel_indices = [i for i in base_indices_set if st.session_state.get(f"chk_{i}")]
                if sel_indices:
                    new_patterns = list(st.session_state.patterns)
                    new_df = st.session_state.df.copy()
                    selected_sizes = st.session_state.get('selected_sizes', [])
                    all_sizes = st.session_state.get('all_sizes', [])

                    # 선택한 패턴 + 모든 선택된 사이즈 확장하여 복사
                    expanded_indices = set()
                    for idx in sel_indices:
                        pattern_group = patterns[idx][4]
                        if pattern_group and pattern_group in group_to_indices:
                            # 선택된 사이즈의 동일 패턴 모두 추가
                            for size_name, size_idx in group_to_indices[pattern_group].items():
                                if not selected_sizes or size_name in selected_sizes:
                                    expanded_indices.add(size_idx)
                        else:
                            expanded_indices.add(idx)

                    for idx in sorted(expanded_indices):
                        orig_fabric = new_df.iloc[idx]["원단"]
                        new_fabric = "복사_" + orig_fabric
                        new_color = get_fabric_color_hex(new_fabric)

                        orig_pattern = st.session_state.patterns[idx]
                        new_patterns.append(orig_pattern)
                        new_row = new_df.iloc[idx].copy()
                        new_row["번호"] = len(new_patterns)
                        new_row["원단"] = new_fabric
                        new_row["형상"] = poly_to_base64(orig_pattern[0], new_color)
                        new_df = pd.concat([new_df, pd.DataFrame([new_row])], ignore_index=True)

                    st.session_state.patterns = new_patterns
                    st.session_state.df = new_df
                    sort_by_fabric()  # 원단 우선 정렬
                    st.rerun()
            if c4.button("🗑삭제", width='stretch', help="선택 패턴 삭제"):
                sel_indices = [i for i in base_indices_set if st.session_state.get(f"chk_{i}")]
                if sel_indices:
                    selected_sizes = st.session_state.get('selected_sizes', [])
                    all_sizes = st.session_state.get('all_sizes', [])
                    new_df = st.session_state.df

                    # 선택한 패턴 + 모든 선택된 사이즈 확장하여 삭제
                    delete_indices = set()
                    for idx in sel_indices:
                        pattern_group = patterns[idx][4]
                        if pattern_group and pattern_group in group_to_indices:
                            for size_name, size_idx in group_to_indices[pattern_group].items():
                                if not selected_sizes or size_name in selected_sizes:
                                    delete_indices.add(size_idx)
                        else:
                            delete_indices.add(idx)

                    keep_indices = [i for i in range(len(patterns)) if i not in delete_indices]
                    new_patterns = [st.session_state.patterns[i] for i in keep_indices]
                    new_df = st.session_state.df.iloc[keep_indices].copy()
                    new_df = new_df.reset_index(drop=True)
                    new_df["번호"] = range(1, len(new_df) + 1)
                    st.session_state.patterns = new_patterns
                    st.session_state.df = new_df
                    sort_by_fabric()  # 원단 우선 정렬
                    st.rerun()

        # 2. 원단명 변경 (선택 패턴의 모든 사이즈에 적용)
        with tool_col2:
            f1, f2 = st.columns([3, 1])
            new_fabric = f1.text_input("원단명", placeholder="예: 안감", label_visibility="collapsed")
            if f2.button("원단적용", width='stretch'):
                sel_indices = [i for i in base_indices_set if st.session_state.get(f"chk_{i}")]
                if sel_indices and new_fabric:
                    new_color = get_fabric_color_hex(new_fabric)
                    selected_sizes = st.session_state.get('selected_sizes', [])
                    all_sizes = st.session_state.get('all_sizes', [])

                    # 선택한 패턴 + 모든 선택된 사이즈 확장하여 원단 변경
                    expanded_indices = set()
                    for idx in sel_indices:
                        pattern_group = patterns[idx][4]
                        if pattern_group and pattern_group in group_to_indices:
                            for size_name, size_idx in group_to_indices[pattern_group].items():
                                if not selected_sizes or size_name in selected_sizes:
                                    expanded_indices.add(size_idx)
                        else:
                            expanded_indices.add(idx)

                    for idx in expanded_indices:
                        st.session_state.df.at[idx, "원단"] = new_fabric
                        st.session_state.df.at[idx, "형상"] = poly_to_base64(patterns[idx][0], new_color)
                    sort_by_fabric()  # 원단 우선 정렬
                    st.rerun()

        # 3. 수량 변경 (선택 패턴의 모든 사이즈에 적용)
        with tool_col3:
            n1, n2 = st.columns([3, 1])
            new_count = n1.number_input("수량", min_value=0, label_visibility="collapsed")
            if n2.button("수량적용", width='stretch'):
                sel_indices = [i for i in base_indices_set if st.session_state.get(f"chk_{i}")]
                if sel_indices:
                    selected_sizes = st.session_state.get('selected_sizes', [])
                    all_sizes = st.session_state.get('all_sizes', [])

                    # 선택한 패턴 + 모든 선택된 사이즈 확장하여 수량 변경
                    expanded_indices = set()
                    for idx in sel_indices:
                        pattern_group = patterns[idx][4]
                        if pattern_group and pattern_group in group_to_indices:
                            for size_name, size_idx in group_to_indices[pattern_group].items():
                                if not selected_sizes or size_name in selected_sizes:
                                    expanded_indices.add(size_idx)
                        else:
                            expanded_indices.add(idx)

                    for idx in expanded_indices:
                        st.session_state.df.at[idx, "수량"] = new_count
                    st.rerun()

        st.divider()

        # ----------------------------------------------------------------
        # C. 썸네일 그리드 (20 Columns Grid) - 기본 사이즈만 표시
        # ----------------------------------------------------------------
        all_sizes = st.session_state.get('all_sizes', [])
        selected_sizes = st.session_state.get('selected_sizes', all_sizes)
        # 기준사이즈: DXF에서 추출한 값 또는 가운데 사이즈
        base_size = st.session_state.get('base_size', selected_sizes[0] if selected_sizes else None)

        if base_size and all_sizes:
            # 기준사이즈 출처 표시
            detected = st.session_state.get('detected_base_size')
            source_info = "(원본)" if detected and detected == base_size else "(중간)"
            st.caption(f"💡 **{base_size}** {source_info} 사이즈 썸네일 (숫자 버튼으로 확대)")
        else:
            st.caption("💡 썸네일 아래 **[숫자 버튼]**을 누르면 확대 창이 열립니다.")

        # 기본 사이즈만 필터링 (썸네일용)
        filtered_patterns = []
        for orig_idx, p_data in enumerate(patterns):
            # 튜플: (poly, pattern_name, fabric_name, size_name, pattern_group, piece_name, dxf_qty, grainline_info)
            poly = p_data[0]
            pattern_name = p_data[1]
            fabric_name = p_data[2]
            size_name = p_data[3]
            pattern_group = p_data[4]
            grainline_info = p_data[7] if len(p_data) > 7 else None
            if not all_sizes:  # 사이즈 없는 DXF
                filtered_patterns.append((orig_idx, poly, pattern_name, fabric_name, size_name, pattern_group, grainline_info))
            elif size_name == base_size:  # 기본 사이즈만
                filtered_patterns.append((orig_idx, poly, pattern_name, fabric_name, size_name, pattern_group, grainline_info))
            elif not size_name:  # 사이즈 없는 패턴
                filtered_patterns.append((orig_idx, poly, pattern_name, fabric_name, size_name, pattern_group, grainline_info))

        cols_per_row = 20
        rows = math.ceil(len(filtered_patterns) / cols_per_row)

        for row in range(rows):
            cols = st.columns(cols_per_row)
            for col_idx in range(cols_per_row):
                list_idx = row * cols_per_row + col_idx
                if list_idx < len(filtered_patterns):
                    orig_idx, p, pattern_name, _, size_name, pattern_group, grainline_info = filtered_patterns[list_idx]
                    # 원단명은 df에서 가져오기 (일괄수정 반영)
                    current_fabric = st.session_state.df.at[orig_idx, "원단"] if orig_idx < len(st.session_state.df) else "겉감"
                    with cols[col_idx]:
                        # 캐싱된 썸네일 사용 (깜빡임 방지, 그레인라인 표시)
                        thumbnail_data = get_cached_thumbnail(orig_idx, p, current_fabric, zoom_span, grainline_info)
                        st.image(thumbnail_data, use_container_width=True)

                        # 팝업 호출 버튼 (순차 번호 - 상세 리스트와 동일)
                        btn_label = f"{list_idx + 1}"
                        if st.button(btn_label, key=f"btn_zoom_{orig_idx}", width='stretch'):
                            show_detail_viewer(orig_idx, p, current_fabric)

                        # 선택 체크박스
                        st.checkbox("선택", key=f"chk_{orig_idx}", label_visibility="collapsed")

        st.divider()

        # ----------------------------------------------------------------
        # D. 하단 작업창: 리스트 & 요척 결과 (Results)
        # ----------------------------------------------------------------
        col1, col2 = st.columns([3, 2])
        
        # [왼쪽] 상세 리스트 (Data Editor) - 기본 사이즈만 편집, 모든 사이즈에 적용
        with col1:
            st.markdown("#### 📝 상세 리스트")

            all_sizes = st.session_state.get('all_sizes', [])
            selected_sizes = st.session_state.get('selected_sizes', all_sizes)

            # 기준사이즈: DXF에서 추출한 값 또는 가운데 사이즈
            base_size = st.session_state.get('base_size', selected_sizes[0] if selected_sizes else None)

            # 패턴 그룹별 인덱스 매핑 (동일 패턴의 다른 사이즈 연결)
            # pattern_group이 같으면 동일 패턴의 다른 사이즈
            group_to_indices = {}  # {pattern_group: {size: idx, ...}}
            for idx, p_data in enumerate(patterns):
                size_name = p_data[3]
                pattern_group = p_data[4]
                if pattern_group:
                    if pattern_group not in group_to_indices:
                        group_to_indices[pattern_group] = {}
                    group_to_indices[pattern_group][size_name] = idx

            # 기본 사이즈 인덱스만 추출 (편집용)
            base_indices = []
            for idx, p_data in enumerate(patterns):
                size_name = p_data[3]
                if not all_sizes:  # 사이즈 없는 DXF
                    base_indices.append(idx)
                elif size_name == base_size:
                    base_indices.append(idx)
                elif not size_name:  # 사이즈 없는 패턴
                    base_indices.append(idx)

            # 선택된 모든 사이즈의 인덱스 (요척 계산용) - DataFrame 기반
            all_filtered_indices = []
            for idx in range(len(st.session_state.df)):
                size_val = st.session_state.df.at[idx, '사이즈'] if '사이즈' in st.session_state.df.columns else ''
                if not all_sizes or not size_val or size_val in selected_sizes:
                    all_filtered_indices.append(idx)
            st.session_state.filtered_indices = all_filtered_indices

            # 기본 사이즈 표시 (출처 표시)
            if base_size and all_sizes:
                detected = st.session_state.get('detected_base_size')
                source_info = "(원본)" if detected and detected == base_size else "(중간)"
                st.caption(f"✏️ **{base_size}** {source_info} 사이즈 편집 → 모든 사이즈에 적용")

            # 데이터프레임 생성 (기본 사이즈만, 사이즈 열 숨김)
            display_df = st.session_state.df.iloc[base_indices].copy()
            display_df["면적(cm²)"] = (display_df["면적_raw"] * 10000).round(1)
            display_df = display_df.drop(columns=["면적_raw"])
            # 사이즈 열 숨김 (사이즈선택 UI에서 이미 선택됨)
            if "사이즈" in display_df.columns:
                display_df = display_df.drop(columns=["사이즈"])
            display_df = display_df.reset_index(drop=True)
            display_df["번호"] = range(1, len(display_df) + 1)  # 번호 순차 재설정

            edited_df = st.data_editor(
                display_df,
                hide_index=True,
                width='stretch',
                num_rows="fixed",
                disabled=["면적(cm²)", "형상"],
                column_config={
                    "형상": st.column_config.ImageColumn(
                        "형상", help="패턴 미리보기", width="small"
                    )
                },
                height=735,
                key="editor_base"
            )

            # 변경사항 감지 및 모든 사이즈에 적용
            if edited_df is not None:
                selected_sizes = st.session_state.get('selected_sizes', [])
                all_sizes = st.session_state.get('all_sizes', [])

                for i in range(len(edited_df)):
                    base_idx = base_indices[i] if i < len(base_indices) else i

                    # 변경 확인
                    old_fabric = st.session_state.df.at[base_idx, "원단"]
                    new_fabric = edited_df.at[i, "원단"]
                    old_qty = st.session_state.df.at[base_idx, "수량"]
                    new_qty = edited_df.at[i, "수량"]
                    old_cat = st.session_state.df.at[base_idx, "구분"]
                    new_cat = edited_df.at[i, "구분"]

                    has_change = (old_fabric != new_fabric or old_qty != new_qty or old_cat != new_cat)

                    if has_change:
                        base_size = patterns[base_idx][3]
                        base_area = st.session_state.df.at[base_idx, "면적_raw"]

                        # 기본 사이즈 업데이트
                        st.session_state.df.at[base_idx, "원단"] = new_fabric
                        st.session_state.df.at[base_idx, "수량"] = new_qty
                        st.session_state.df.at[base_idx, "구분"] = new_cat
                        if old_fabric != new_fabric:
                            st.session_state.df.at[base_idx, "형상"] = poly_to_base64(patterns[base_idx][0], get_fabric_color_hex(new_fabric))

                        # 동일 패턴의 다른 사이즈에도 적용 (pattern_group으로 매칭)
                        base_pattern_group = patterns[base_idx][4] if base_idx < len(patterns) else None
                        if base_pattern_group and base_pattern_group in group_to_indices:
                            for size_name, j in group_to_indices[base_pattern_group].items():
                                if j == base_idx:
                                    continue
                                # 선택된 사이즈인 경우만 적용
                                if not all_sizes or not size_name or size_name in selected_sizes:
                                    st.session_state.df.at[j, "원단"] = new_fabric
                                    st.session_state.df.at[j, "수량"] = new_qty
                                    st.session_state.df.at[j, "구분"] = new_cat
                                    if old_fabric != new_fabric and j < len(patterns):
                                        st.session_state.df.at[j, "형상"] = poly_to_base64(patterns[j][0], get_fabric_color_hex(new_fabric))

                        # 구분 변경 시 네스팅 결과의 패턴 이름도 업데이트
                        if old_cat != new_cat:
                            update_nesting_pattern_names()

                        # 변경 후 새로고침하여 요척결과 갱신
                        st.rerun()

        # [오른쪽] 요척 결과 카드 (Compact View) - 사이즈별 표시
        with col2:
            st.markdown("#### 📊 요척 결과")

            # 헤더 라벨
            h1, h2, h_u, h3, h4 = st.columns([1.4, 0.9, 0.9, 0.9, 1.4])
            h1.caption("원단/사이즈")
            h2.caption("폭(W)")
            h_u.caption("단위")
            h3.caption("로스(%)")
            h4.caption("필요요척(YD)")

            # 데이터 재계산 (필터링된 데이터 사용)
            filtered_indices = st.session_state.get('filtered_indices', list(range(len(st.session_state.df))))
            calc_df = st.session_state.df.iloc[filtered_indices].copy()

            # 선택된 사이즈 목록
            selected_sizes = st.session_state.get('selected_sizes', [])
            all_sizes_in_file = st.session_state.get('all_sizes', [])

            # 원단별 그룹
            fabric_groups = calc_df.groupby("원단")

            # 전체 합계 저장용
            total_yield_all = 0.0
            fabric_settings = {}  # 원단별 설정 저장

            fab_idx = 0
            for fabric_name, fabric_group in fabric_groups:
                color = get_fabric_color_hex(fabric_name)

                # 원단 헤더 (설정 입력)
                with st.container(border=True):
                    c1, c2, c_unit, c3, c4 = st.columns([1.4, 0.9, 0.9, 0.9, 1.4])

                    with c1:
                        st.markdown(f"""
                        <div style='background-color:{color}; padding:5px 0px; border-radius:4px; text-align:center;'>
                            <strong style='font-size:14px; color:#333;'>{fabric_name}</strong>
                        </div>""", unsafe_allow_html=True)

                    with c2:
                        input_width = st.number_input("W", value=58.00, min_value=10.0, step=0.1, format="%.2f", key=f"w_{fabric_name}", label_visibility="collapsed")

                    with c_unit:
                        unit = st.selectbox("U", ["in", "cm"], key=f"unit_{fabric_name}", label_visibility="collapsed")

                    with c3:
                        input_loss = st.number_input("L", value=15, min_value=0, key=f"l_{fabric_name}", label_visibility="collapsed")

                    # 설정 저장 (원단명 기반)
                    fabric_settings[fabric_name] = {
                        'width': input_width,
                        'unit': unit,
                        'loss': input_loss
                    }

                    # 원단 전체 합계 계산
                    if all_sizes_in_file and selected_sizes:
                        # 사이즈가 있는 경우: 선택된 사이즈만 합산
                        fabric_total_yd = 0.0
                        for size in selected_sizes:
                            size_data = fabric_group[fabric_group['사이즈'] == size]
                            if not size_data.empty:
                                size_area = sum(row['면적_raw'] * row['수량'] for _, row in size_data.iterrows())
                                if input_width > 0:
                                    width_m = input_width / 100 if unit == "cm" else (input_width * 2.54) / 100
                                    size_yd = ((size_area / width_m) / ((100-input_loss)/100)) * 1.09361
                                    fabric_total_yd += size_yd

                        with c4:
                            st.markdown(f"""
                            <div style='text-align:right; padding-top:5px;'>
                                <span style='font-size:16px; color:#0068c9; font-weight:bold;'>{fabric_total_yd:.2f} YD</span>
                            </div>""", unsafe_allow_html=True)

                        total_yield_all += fabric_total_yd
                    else:
                        # 사이즈 없는 경우: 전체 합산
                        group_area = sum(row['면적_raw'] * row['수량'] for _, row in fabric_group.iterrows())
                        if input_width > 0:
                            width_m = input_width / 100 if unit == "cm" else (input_width * 2.54) / 100
                            req_yd = ((group_area / width_m) / ((100-input_loss)/100)) * 1.09361
                        else:
                            req_yd = 0

                        with c4:
                            st.markdown(f"""
                            <div style='text-align:right; padding-top:5px;'>
                                <span style='font-size:18px; color:#0068c9; font-weight:bold;'>{req_yd:.2f} YD</span>
                            </div>""", unsafe_allow_html=True)

                        total_yield_all += req_yd

                # 사이즈별 상세 (사이즈가 있는 경우만)
                if all_sizes_in_file and selected_sizes:
                    for size in selected_sizes:
                        size_data = fabric_group[fabric_group['사이즈'] == size]
                        if not size_data.empty:
                            size_area = sum(row['면적_raw'] * row['수량'] for _, row in size_data.iterrows())

                            if input_width > 0:
                                width_m = input_width / 100 if unit == "cm" else (input_width * 2.54) / 100
                                size_yd = ((size_area / width_m) / ((100-input_loss)/100)) * 1.09361
                            else:
                                size_yd = 0

                            # 사이즈별 행 (들여쓰기)
                            s1, s2, s3, s4, s5 = st.columns([1.4, 0.9, 0.9, 0.9, 1.4])
                            with s1:
                                st.markdown(f"""
                                <div style='padding-left:20px; color:#666;'>
                                    └ {size}
                                </div>""", unsafe_allow_html=True)
                            with s5:
                                st.markdown(f"""
                                <div style='text-align:right; color:#666;'>
                                    {size_yd:.2f} YD
                                </div>""", unsafe_allow_html=True)

                fab_idx += 1

            # 전체 합계 표시
            st.markdown("---")
            t1, t2, t3, t4, t5 = st.columns([1.4, 0.9, 0.9, 0.9, 1.4])
            with t1:
                st.markdown("""
                <div style='padding:5px 0px; text-align:center;'>
                    <strong style='font-size:14px;'>합계</strong>
                </div>""", unsafe_allow_html=True)
            with t5:
                st.markdown(f"""
                <div style='text-align:right; padding-top:5px;'>
                    <span style='font-size:20px; color:#d94a4a; font-weight:bold;'>{total_yield_all:.2f} YD</span>
                </div>""", unsafe_allow_html=True)

            # 설정값 세션에 저장 (엑셀 내보내기용)
            st.session_state.fabric_settings = fabric_settings

            # ----------------------------------------------------------------
            # E. 엑셀 다운로드 버튼
            # ----------------------------------------------------------------
            st.divider()

            # 요척 결과 데이터 수집 (사이즈별)
            yield_data = []
            total_yield = 0.0

            # 설정값 가져오기
            fabric_settings = st.session_state.get('fabric_settings', {})
            selected_sizes = st.session_state.get('selected_sizes', [])
            all_sizes_in_file = st.session_state.get('all_sizes', [])

            for fabric_name, fabric_group in fabric_groups:
                settings = fabric_settings.get(fabric_name, {})
                input_width = settings.get('width', 58.0)
                unit = settings.get('unit', 'in')
                input_loss = settings.get('loss', 15)

                if all_sizes_in_file and selected_sizes:
                    # 사이즈별 행 추가
                    fabric_total = 0.0
                    for size in selected_sizes:
                        size_data = fabric_group[fabric_group['사이즈'] == size]
                        if not size_data.empty:
                            size_area = sum(row['면적_raw'] * row['수량'] for _, row in size_data.iterrows())
                            if input_width > 0:
                                width_m = input_width / 100 if unit == "cm" else (input_width * 2.54) / 100
                                size_yd = ((size_area / width_m) / ((100-input_loss)/100)) * 1.09361
                            else:
                                size_yd = 0

                            yield_data.append({
                                "원단명": fabric_name,
                                "사이즈": size,
                                "폭": input_width,
                                "단위": unit,
                                "효율(%)": 100 - input_loss,
                                "필요요척(YD)": round(size_yd, 2)
                            })
                            fabric_total += size_yd

                    # 원단 소계
                    yield_data.append({
                        "원단명": f"{fabric_name} 소계",
                        "사이즈": "",
                        "폭": "",
                        "단위": "",
                        "효율(%)": "",
                        "필요요척(YD)": round(fabric_total, 2)
                    })
                    total_yield += fabric_total
                else:
                    # 사이즈 없는 경우
                    group_area = sum(row['면적_raw'] * row['수량'] for _, row in fabric_group.iterrows())
                    if input_width > 0:
                        width_m = input_width / 100 if unit == "cm" else (input_width * 2.54) / 100
                        req_yd = ((group_area / width_m) / ((100-input_loss)/100)) * 1.09361
                    else:
                        req_yd = 0

                    yield_data.append({
                        "원단명": fabric_name,
                        "사이즈": "",
                        "폭": input_width,
                        "단위": unit,
                        "효율(%)": 100 - input_loss,
                        "필요요척(YD)": round(req_yd, 2)
                    })
                    total_yield += req_yd

            # 전체 합계 행 추가
            yield_data.append({
                "원단명": "합계",
                "사이즈": "",
                "폭": "",
                "단위": "",
                "효율(%)": "",
                "필요요척(YD)": round(total_yield, 2)
            })

            yield_df = pd.DataFrame(yield_data)

            # 엑셀 파일 생성
            excel_buffer = io.BytesIO()
            file_name = uploaded_file.name.replace('.dxf', '').replace('.DXF', '')
            style_no = st.session_state.get('style_no', '')

            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                # 시트1: 상세리스트 (선택된 모든 사이즈 포함)
                export_df = calc_df.copy()
                export_df["면적(cm²)"] = (export_df["면적_raw"] * 10000).round(1)
                detail_df = export_df.drop(columns=["형상", "면적_raw"], errors='ignore')
                # 파일명, 스타일번호 컬럼 추가
                detail_df.insert(0, "스타일번호", style_no)
                detail_df.insert(0, "파일명", file_name)
                detail_df.to_excel(writer, sheet_name='상세리스트', index=False)

                # 시트2: 요척결과 (사이즈별)
                # 파일명, 스타일번호 컬럼 추가
                yield_df.insert(0, "스타일번호", style_no)
                yield_df.insert(0, "파일명", file_name)
                yield_df.to_excel(writer, sheet_name='요척결과', index=False)

            excel_buffer.seek(0)

            st.download_button(
                label="📥 엑셀 다운로드",
                data=excel_buffer,
                file_name=f"{file_name}_요척결과.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width='stretch'
            )

        # ----------------------------------------------------------------
        # F. 네스팅 시뮬레이션 (원단별) - 전체 폭 사용
        # ----------------------------------------------------------------
        st.divider()
        st.markdown("#### 🧩 네스팅 시뮬레이션")

        # 원단별 설정
        fabric_list = st.session_state.df['원단'].unique().tolist()
        fabric_widths = {}
        marker_quantities = {}  # 원단별 벌수 (사이즈 없을 때 사용)
        size_quantities = {}    # 사이즈별 벌수
        target_efficiencies = {}

        # 사이즈 목록 확인
        all_sizes = st.session_state.get('all_sizes', [])
        selected_sizes = st.session_state.get('selected_sizes', all_sizes)
        has_multiple_sizes = len(selected_sizes) >= 2

        # 2컬럼 레이아웃: 왼쪽(공통설정) | 오른쪽(180도회전 + 원단별설정)
        left_col, right_col = st.columns(2)

        # 원단별 패턴 버퍼 저장용 딕셔너리
        fabric_buffers = {}

        with left_col:
            # 공통 설정 (기본 패턴 버퍼 - 원단별 미설정 시 사용)
            nest_buffer = st.number_input(
                "기본 패턴 버퍼 (mm)",
                min_value=0, max_value=50, value=0,
                help="패턴 둘레로 확장되는 여유 공간 (블로킹)",
                key="nest_buffer"
            )

            # 180도 회전 허용
            nest_rotation = st.checkbox(
                "180도 회전 허용",
                value=True,
                help="패턴을 180도 회전하여 배치",
                key="nest_rotation"
            )

            # 좌우 마주 보기 (좌우 미러링)
            nest_mirror = st.checkbox(
                "좌우 마주 보기",
                value=False,
                help="수량 2개 이상 패턴을 좌우 뒤집어서 마주보게 배치",
                key="nest_mirror"
            )

            # Sparrow 모드 (항상 활성화, UI 숨김)
            use_sparrow = SPARROW_AVAILABLE

            if use_sparrow:
                sparrow_time = st.number_input(
                    "최적화 시간(초)",
                    min_value=5, max_value=120, value=30,
                    help="더 긴 시간 = 더 좋은 결과",
                    key="sparrow_time"
                )
            else:
                sparrow_time = 30

            # 네스팅 실행 버튼
            run_nesting = st.button("🚀 네스팅 실행", width='stretch', type="primary")
            if 'nesting_elapsed' in st.session_state:
                st.caption(f"⏱️ {st.session_state.nesting_elapsed:.1f}초")

        with right_col:

            # 원단별 설정 헤더
            hcol1, hcol2, hcol3, hcol4 = st.columns([2, 1, 1, 1])
            with hcol1:
                st.markdown("**원단**")
            with hcol2:
                st.markdown("**효율%**")
            with hcol3:
                st.markdown("**버퍼mm**")
            with hcol4:
                st.markdown("**벌수**")

            # 원단별 설정 입력
            for i, fabric in enumerate(fabric_list):
                # 요척 결과에서 설정한 폭과 단위 가져오기 (원단명 기반 키)
                width_val = st.session_state.get(f"w_{fabric}", 58.0)
                unit_val = st.session_state.get(f"unit_{fabric}", "in")
                # cm로 변환
                if unit_val == "in":
                    width_cm = width_val * 2.54
                else:
                    width_cm = width_val
                fabric_widths[fabric] = width_cm

                col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                with col1:
                    st.text(f"{fabric}: {width_cm:.1f}cm")
                with col2:
                    target_efficiencies[fabric] = st.number_input(
                        "효율%",
                        min_value=60, max_value=95, value=80,
                        key=f"target_eff_{i}",
                        label_visibility="collapsed"
                    )
                with col3:
                    # 원단별 패턴 버퍼 (기본값: 공통 설정값 사용)
                    fabric_buffers[fabric] = st.number_input(
                        "버퍼",
                        min_value=0, max_value=50, value=nest_buffer,
                        key=f"fabric_buffer_{i}",
                        label_visibility="collapsed",
                        help=f"{fabric} 원단의 패턴 버퍼 (둘레 확장)"
                    )
                with col4:
                    marker_quantities[fabric] = st.number_input(
                        "벌수",
                        min_value=1, max_value=10, value=1,
                        key=f"marker_qty_{i}",
                        label_visibility="collapsed",
                        disabled=has_multiple_sizes  # 사이즈별 벌수 사용시 비활성화
                    )

            # 사이즈별 벌수 설정 (사이즈가 2개 이상일 때만 표시)
            if has_multiple_sizes:
                st.markdown("---")
                st.markdown("**📏 사이즈별 벌수**")

                # 사이즈별 벌수 입력 (가로 배치)
                size_cols = st.columns(len(selected_sizes))
                for si, size in enumerate(selected_sizes):
                    with size_cols[si]:
                        size_quantities[size] = st.number_input(
                            size,
                            min_value=0, max_value=10, value=1,
                            key=f"size_qty_{si}",
                            help=f"{size} 사이즈 벌수 (0=제외)"
                        )

        if run_nesting:
            import time
            start_time = time.time()
            spinner_msg = "🐦 Sparrow 최적화 중..." if use_sparrow else "원단별 네스팅 계산 중..."
            with st.spinner(spinner_msg):
                try:
                    nesting_results = {}

                    # 선택된 사이즈로 필터링된 인덱스 (상세 리스트와 동일)
                    all_sizes = st.session_state.get('all_sizes', [])
                    selected_sizes = st.session_state.get('selected_sizes', all_sizes)

                    filtered_indices_for_nesting = []
                    for idx, p_data in enumerate(patterns):
                        size_name = p_data[3]  # (poly, pattern_name, fabric_name, size_name, pattern_group)
                        if not all_sizes or not size_name or size_name in selected_sizes:
                            filtered_indices_for_nesting.append(idx)

                    # 원단별로 네스팅 실행
                    for fabric in fabric_list:
                        # 해당 원단 + 선택된 사이즈의 패턴만 필터링
                        fabric_indices = [
                            idx for idx in filtered_indices_for_nesting
                            if st.session_state.df.loc[idx, '원단'] == fabric
                        ]

                        if len(fabric_indices) == 0:
                            continue

                        # 패턴 데이터 수집
                        fabric_marker_qty = marker_quantities.get(fabric, 1)
                        pattern_data = []
                        total_qty_debug = 0  # 디버그: 총 수량 추적
                        for idx in fabric_indices:
                            if idx < len(patterns):
                                row = st.session_state.df.loc[idx]
                                poly = patterns[idx][0]
                                size_name = patterns[idx][3]  # 사이즈 정보
                                grainline_info = patterns[idx][7] if len(patterns[idx]) > 7 else None  # 그레인라인 정보
                                coords = list(poly.exterior.coords)[:-1]
                                coords_cm = [(p[0] / 10, p[1] / 10) for p in coords]

                                # 그레인라인 좌표도 cm로 변환
                                grainline_cm = None
                                if grainline_info:
                                    gl_start, gl_end = grainline_info
                                    grainline_cm = ((gl_start[0]/10, gl_start[1]/10), (gl_end[0]/10, gl_end[1]/10))

                                # 사이즈별 벌수 적용 (사이즈 2개 이상일 때)
                                if has_multiple_sizes and size_name:
                                    size_qty = size_quantities.get(size_name, 1)
                                    if size_qty == 0:  # 벌수 0이면 제외
                                        continue
                                    quantity = int(row['수량']) * size_qty
                                else:
                                    quantity = int(row['수량']) * fabric_marker_qty

                                total_qty_debug += quantity  # 디버그: 수량 누적

                                # 패턴ID: df인덱스(고유) + 이름(표시용) + 사이즈
                                # df인덱스는 정렬 후에도 유지되는 고유 식별자
                                pattern_name = str(row['구분'])[:8] if row['구분'] else ""  # 표시용 이름
                                pattern_id = f"{idx}:{pattern_name}\n{size_name[:4]}" if size_name else f"{idx}:{pattern_name}"
                                pattern_data.append({
                                    'coords_cm': coords_cm,
                                    'quantity': quantity,
                                    'pattern_id': pattern_id,
                                    'area_cm2': poly.area / 100,
                                    'df_idx': idx,  # 원본 df 인덱스 저장
                                    'grainline_cm': grainline_cm  # 그레인라인 좌표 (cm)
                                })

                        # 디버그: 상세 리스트 수량 합계 계산
                        df_fabric_indices = [i for i in range(len(st.session_state.df)) if st.session_state.df.loc[i, '원단'] == fabric]
                        df_qty_sum = sum(int(st.session_state.df.loc[i, '수량']) for i in df_fabric_indices)

                        # 사이즈별 벌수 적용한 예상 수량
                        if has_multiple_sizes:
                            expected_qty = df_qty_sum * sum(size_quantities.get(s, 1) for s in size_quantities if size_quantities.get(s, 1) > 0)
                        else:
                            expected_qty = df_qty_sum * fabric_marker_qty

                        st.info(f"📊 {fabric}: 상세리스트 {len(df_fabric_indices)}개(수량합:{df_qty_sum}), 네스팅 {len(pattern_data)}종(총:{total_qty_debug}개)")

                        if total_qty_debug != expected_qty and has_multiple_sizes:
                            st.warning(f"⚠️ 수량 불일치: 예상 {expected_qty}개, 실제 {total_qty_debug}개")

                        width_cm = fabric_widths[fabric]
                        # 원단별 패턴 버퍼 (미설정 시 기본값 사용)
                        fabric_buffer = fabric_buffers.get(fabric, nest_buffer)

                        if use_sparrow and SPARROW_AVAILABLE:
                            # Sparrow 네스팅 (버퍼로 패턴 둘레 확장)
                            result = run_sparrow_nesting(
                                pattern_data, width_cm, sparrow_time, nest_rotation, 0, nest_mirror, fabric_buffer
                            )
                        else:
                            # 기본 네스팅 엔진 (버퍼 미지원, spacing으로 대체)
                            fabric_target_eff = target_efficiencies.get(fabric, 80)
                            engine = NestingEngine(
                                sheet_width=width_cm * 10,
                                spacing=fabric_buffer,  # 기본 엔진은 spacing으로 대체
                                target_efficiency=fabric_target_eff
                            )
                            for p in pattern_data:
                                engine.add_pattern(
                                    list(p['coords_cm']),
                                    quantity=p['quantity'],
                                    pattern_id=p['pattern_id']
                                )
                            rotations = [0, 180] if nest_rotation else [0]
                            result = engine.run(rotations=rotations)

                        result['fabric'] = fabric
                        result['width_cm'] = width_cm
                        result['marker_quantity'] = fabric_marker_qty
                        result['size_quantities'] = size_quantities if has_multiple_sizes else {}
                        result['has_multiple_sizes'] = has_multiple_sizes
                        result['buffer'] = fabric_buffer  # 원단별 패턴 버퍼 저장
                        nesting_results[fabric] = result

                        # 디버그: 배치 실패 패턴 확인
                        placed = result.get('placed_count', 0)
                        total = result.get('total_count', 0)
                        if placed < total:
                            st.error(f"❌ {fabric}: 배치 실패! 입력 {total}개 중 {placed}개만 배치됨 ({total - placed}개 누락)")

                    # 결과 저장 (작업일시 + 실행시간 추가)
                    from datetime import datetime
                    st.session_state.nesting_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                    st.session_state.nesting_elapsed = time.time() - start_time
                    st.session_state.nesting_results = nesting_results
                    st.rerun()

                except Exception as e:
                    st.error(f"네스팅 오류: {str(e)}")
                    import traceback
                    st.code(traceback.format_exc())

        # 네스팅 결과 표시 (원단별 - 2열 배치)
        if 'nesting_results' in st.session_state and st.session_state.nesting_results:
            results = st.session_state.nesting_results
            fabric_list_results = list(results.keys())

            # 2열로 배치
            for i in range(0, len(fabric_list_results), 2):
                cols = st.columns(2)

                for j, col in enumerate(cols):
                    if i + j < len(fabric_list_results):
                        fabric = fabric_list_results[i + j]
                        result = results[fabric]
                        color = get_fabric_color_hex(fabric)
                        marker_qty = result.get('marker_quantity', 1)
                        timestamp = st.session_state.get('nesting_timestamp', '')

                        # 선택된 사이즈 정보 표시 (예: S/2,M/3=5벌)
                        selected_sizes = st.session_state.get('selected_sizes', [])
                        result_size_qty = result.get('size_quantities', {})
                        result_has_multi = result.get('has_multiple_sizes', False)

                        if selected_sizes and result_has_multi and result_size_qty:
                            # 사이즈별 벌수 표시
                            size_parts = ','.join([f"{s}/{result_size_qty.get(s, 1)}" for s in selected_sizes if result_size_qty.get(s, 1) > 0])
                            total_qty = sum(result_size_qty.get(s, 1) for s in selected_sizes if result_size_qty.get(s, 1) > 0)
                            size_info = f"{size_parts}={total_qty}벌"
                        elif selected_sizes:
                            size_parts = ','.join([f"{s}/{marker_qty}" for s in selected_sizes])
                            total_qty = len(selected_sizes) * marker_qty
                            size_info = f"{size_parts}={total_qty}벌"
                        else:
                            size_info = f"{marker_qty}벌"

                        with col:
                            with st.expander(f"📦 {fabric}({size_info}) - {timestamp}", expanded=True):
                                if result['success']:
                                    # 결과 메트릭 (5열: 패턴수, 원단폭, 마카길이, 요척, 효율)
                                    m1, m2, m3, m4, m5 = st.columns([1, 1, 1.2, 1, 0.8])
                                    m1.metric("패턴수", f"{result['placed_count']}/{result['total_count']}")
                                    m2.metric("원단폭", f"{result['width_cm']:.0f} cm")
                                    m3.metric("마카길이", f"{result['used_length_cm']:.1f} cm")
                                    # 요척 = 마카길이(YD) / 벌수
                                    yield_per_set = result['used_length_yd'] / marker_qty
                                    m4.metric("요척", f"{yield_per_set:.2f} YD")
                                    m5.metric("효율", f"{result['efficiency']}%")

                                    # 시각화
                                    try:
                                        if result.get('sparrow_mode'):
                                            fig = create_sparrow_visualization(result, result['width_cm'])
                                        else:
                                            fig = create_nesting_visualization(result, result['width_cm'])
                                        if fig:
                                            st.pyplot(fig)
                                            plt.close(fig)
                                    except Exception as e:
                                        st.warning(f"시각화 오류: {str(e)}")

                                    # 재네스팅 옵션
                                    st.markdown("---")

                                    # 사이즈별 벌수 설정 (사이즈 2개 이상일 때)
                                    re_all_sizes = st.session_state.get('all_sizes', [])
                                    re_selected_sizes = st.session_state.get('selected_sizes', re_all_sizes)
                                    re_has_multi = len(re_selected_sizes) >= 2

                                    re_size_quantities = {}
                                    if re_has_multi:
                                        st.markdown("**사이즈별 벌수**")
                                        re_size_cols = st.columns(len(re_selected_sizes))
                                        for si, size in enumerate(re_selected_sizes):
                                            with re_size_cols[si]:
                                                re_size_quantities[size] = st.number_input(
                                                    size,
                                                    min_value=0, max_value=10, value=1,
                                                    key=f"re_sz_{fabric}_{size}_{i+j}",
                                                    help=f"{size} 벌수 (0=제외)"
                                                )

                                    # 벌수 변경 (사이즈 1개일 때만 표시)
                                    if not re_has_multi:
                                        re_col1, re_col2 = st.columns([1, 1])
                                        with re_col1:
                                            new_qty = st.number_input(
                                                "벌수 변경",
                                                min_value=1, max_value=10,
                                                value=marker_qty,
                                                key=f"re_qty_{fabric}_{i+j}"
                                            )
                                        with re_col2:
                                            re_nest_btn = st.button("🔄 재네스팅", key=f"re_nest_{fabric}_{i+j}", width='stretch')
                                    else:
                                        new_qty = 1  # 사이즈별 벌수 사용 시 기본값
                                        re_nest_btn = st.button("🔄 재네스팅", key=f"re_nest_{fabric}_{i+j}", width='stretch')

                                    if re_nest_btn:
                                            # 해당 원단만 재네스팅
                                            with st.spinner(f"🐦 {fabric} 재네스팅 중..."):
                                                try:
                                                    import time
                                                    start_time = time.time()

                                                    # 선택된 사이즈 + 해당 원단의 패턴만 필터링
                                                    all_sizes = st.session_state.get('all_sizes', [])
                                                    selected_sizes = st.session_state.get('selected_sizes', all_sizes)

                                                    fabric_indices = []
                                                    for idx, p_data in enumerate(patterns):
                                                        size_name = p_data[3]
                                                        if st.session_state.df.loc[idx, '원단'] == fabric:
                                                            if not all_sizes or not size_name or size_name in selected_sizes:
                                                                fabric_indices.append(idx)

                                                    # 패턴 데이터 수집
                                                    pattern_data = []
                                                    for idx in fabric_indices:
                                                        if idx < len(patterns):
                                                            row = st.session_state.df.loc[idx]
                                                            poly = patterns[idx][0]
                                                            size_name = patterns[idx][3]
                                                            grainline_info = patterns[idx][7] if len(patterns[idx]) > 7 else None
                                                            coords = list(poly.exterior.coords)[:-1]
                                                            coords_cm = [(p[0] / 10, p[1] / 10) for p in coords]

                                                            # 그레인라인 좌표도 cm로 변환
                                                            grainline_cm = None
                                                            if grainline_info:
                                                                gl_start, gl_end = grainline_info
                                                                grainline_cm = ((gl_start[0]/10, gl_start[1]/10), (gl_end[0]/10, gl_end[1]/10))

                                                            # 사이즈별 벌수 적용
                                                            if re_has_multi and size_name:
                                                                sz_qty = re_size_quantities.get(size_name, 1)
                                                                if sz_qty == 0:
                                                                    continue
                                                                quantity = int(row['수량']) * sz_qty
                                                            else:
                                                                quantity = int(row['수량']) * new_qty

                                                            base_id = str(row['구분'])[:12] if row['구분'] else f"P{idx+1}"
                                                            pattern_id = f"{base_id}\n{size_name[:4]}" if size_name else base_id
                                                            pattern_data.append({
                                                                'coords_cm': coords_cm,
                                                                'quantity': quantity,
                                                                'pattern_id': pattern_id,
                                                                'area_cm2': poly.area / 100,
                                                                'grainline_cm': grainline_cm
                                                            })

                                                    width_cm = result['width_cm']
                                                    # 원단별 패턴 버퍼 (저장된 값 또는 기본값)
                                                    fabric_buffer = result.get('buffer', st.session_state.get('nest_buffer', 0))

                                                    # Sparrow 네스팅 실행 (버퍼로 패턴 둘레 확장)
                                                    if SPARROW_AVAILABLE:
                                                        new_result = run_sparrow_nesting(
                                                            pattern_data, width_cm,
                                                            st.session_state.get('sparrow_time', 30),
                                                            st.session_state.get('nest_rotation', True),
                                                            0,
                                                            st.session_state.get('nest_mirror', False),
                                                            fabric_buffer
                                                        )
                                                    else:
                                                        engine = NestingEngine(
                                                            sheet_width=width_cm * 10,
                                                            spacing=fabric_buffer,  # 기본 엔진은 spacing으로 대체
                                                            target_efficiency=80
                                                        )
                                                        for p in pattern_data:
                                                            engine.add_pattern(
                                                                list(p['coords_cm']),
                                                                quantity=p['quantity'],
                                                                pattern_id=p['pattern_id']
                                                            )
                                                        rotations = [0, 180] if st.session_state.get('nest_rotation', True) else [0]
                                                        new_result = engine.run(rotations=rotations)

                                                    new_result['fabric'] = fabric
                                                    new_result['width_cm'] = width_cm
                                                    new_result['marker_quantity'] = new_qty
                                                    new_result['size_quantities'] = re_size_quantities if re_has_multi else {}
                                                    new_result['has_multiple_sizes'] = re_has_multi
                                                    new_result['buffer'] = fabric_buffer  # 원단별 패턴 버퍼 유지

                                                    # 결과 업데이트
                                                    st.session_state.nesting_results[fabric] = new_result
                                                    st.session_state.nesting_elapsed = time.time() - start_time
                                                    from datetime import datetime
                                                    st.session_state.nesting_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                                                    st.rerun()

                                                except Exception as e:
                                                    st.error(f"재네스팅 오류: {str(e)}")
                                else:
                                    st.warning(f"{fabric}: 패턴을 배치할 수 없습니다.")

            # 자동 최적화 버튼 (효율 70% 미만 원단 자동 벌수 조정)
            st.markdown("---")
            low_eff_fabrics = [f for f, r in results.items() if r.get('success') and r.get('efficiency', 0) < 70]

            opt_col1, opt_col2 = st.columns([1, 1])
            with opt_col1:
                if low_eff_fabrics:
                    st.caption(f"⚠️ 효율 70% 미만: {', '.join(low_eff_fabrics)}")
                else:
                    st.caption("✅ 모든 원단 효율 70% 이상")

            with opt_col2:
                if st.button("🎯 자동 최적화", width='stretch', disabled=len(low_eff_fabrics)==0):
                    with st.spinner("🔄 효율 70% 미만 원단 자동 최적화 중..."):
                        try:
                            import time
                            start_time = time.time()
                            optimized_count = 0

                            for fabric in low_eff_fabrics:
                                original_result = results[fabric]
                                width_cm = original_result['width_cm']
                                original_qty = original_result.get('marker_quantity', 1)
                                best_result = original_result
                                best_efficiency = original_result.get('efficiency', 0)
                                best_qty = original_qty

                                # 선택된 사이즈 + 해당 원단의 패턴 데이터 준비
                                all_sizes = st.session_state.get('all_sizes', [])
                                selected_sizes = st.session_state.get('selected_sizes', all_sizes)

                                fabric_indices = []
                                for idx, p_data in enumerate(patterns):
                                    size_name = p_data[3]
                                    if st.session_state.df.loc[idx, '원단'] == fabric:
                                        if not all_sizes or not size_name or size_name in selected_sizes:
                                            fabric_indices.append(idx)

                                base_pattern_data = []
                                for idx in fabric_indices:
                                    if idx < len(patterns):
                                        row = st.session_state.df.loc[idx]
                                        poly = patterns[idx][0]
                                        size_name = patterns[idx][3]
                                        grainline_info = patterns[idx][7] if len(patterns[idx]) > 7 else None
                                        coords = list(poly.exterior.coords)[:-1]
                                        coords_cm = [(p[0] / 10, p[1] / 10) for p in coords]

                                        # 그레인라인 좌표도 cm로 변환
                                        grainline_cm = None
                                        if grainline_info:
                                            gl_start, gl_end = grainline_info
                                            grainline_cm = ((gl_start[0]/10, gl_start[1]/10), (gl_end[0]/10, gl_end[1]/10))

                                        base_id = str(row['구분'])[:12] if row['구분'] else f"P{idx+1}"
                                        pattern_id = f"{base_id}\n{size_name[:4]}" if size_name else base_id
                                        base_pattern_data.append({
                                            'coords_cm': coords_cm,
                                            'base_quantity': int(row['수량']),
                                            'pattern_id': pattern_id,
                                            'area_cm2': poly.area / 100,
                                            'grainline_cm': grainline_cm
                                        })

                                # 원단별 패턴 버퍼 (저장된 값 또는 기본값)
                                fabric_buffer = result.get('buffer', st.session_state.get('nest_buffer', 0))

                                # 벌수 2~5까지 시도하여 최적 효율 찾기
                                for try_qty in range(2, 6):
                                    pattern_data = []
                                    for p in base_pattern_data:
                                        pattern_data.append({
                                            'coords_cm': p['coords_cm'],
                                            'quantity': p['base_quantity'] * try_qty,
                                            'pattern_id': p['pattern_id'],
                                            'area_cm2': p['area_cm2'],
                                            'grainline_cm': p.get('grainline_cm')
                                        })

                                    # 네스팅 실행 (버퍼로 패턴 둘레 확장)
                                    if SPARROW_AVAILABLE:
                                        test_result = run_sparrow_nesting(
                                            pattern_data, width_cm,
                                            st.session_state.get('sparrow_time', 30),
                                            st.session_state.get('nest_rotation', True),
                                            0,
                                            st.session_state.get('nest_mirror', False),
                                            fabric_buffer
                                        )
                                    else:
                                        engine = NestingEngine(
                                            sheet_width=width_cm * 10,
                                            spacing=fabric_buffer,  # 기본 엔진은 spacing으로 대체
                                            target_efficiency=80
                                        )
                                        for p in pattern_data:
                                            engine.add_pattern(
                                                list(p['coords_cm']),
                                                quantity=p['quantity'],
                                                pattern_id=p['pattern_id']
                                            )
                                        rotations = [0, 180] if st.session_state.get('nest_rotation', True) else [0]
                                        test_result = engine.run(rotations=rotations)

                                    test_eff = test_result.get('efficiency', 0)

                                    # 더 좋은 효율이면 저장
                                    if test_eff > best_efficiency:
                                        best_efficiency = test_eff
                                        best_result = test_result
                                        best_qty = try_qty

                                    # 목표 효율(80%) 이상이면 중단
                                    if test_eff >= 80:
                                        break

                                # 최적 결과로 업데이트
                                if best_qty != original_qty:
                                    best_result['fabric'] = fabric
                                    best_result['width_cm'] = width_cm
                                    best_result['marker_quantity'] = best_qty
                                    st.session_state.nesting_results[fabric] = best_result
                                    optimized_count += 1

                            st.session_state.nesting_elapsed = time.time() - start_time
                            from datetime import datetime
                            st.session_state.nesting_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

                            if optimized_count > 0:
                                st.success(f"✅ {optimized_count}개 원단 최적화 완료!")
                                st.rerun()
                            else:
                                st.info("최적화할 원단이 없습니다.")

                        except Exception as e:
                            st.error(f"자동 최적화 오류: {str(e)}")
                            import traceback
                            st.code(traceback.format_exc())

            # 네스팅 결과 엑셀 내보내기
            st.markdown("---")
            excel_data = export_nesting_to_excel(results, st.session_state.get('nesting_timestamp', ''))
            if excel_data:
                # DXF 파일 이름에서 확장자 제거
                dxf_base_name = uploaded_file.name.replace('.dxf', '').replace('.DXF', '')
                st.download_button(
                    label="📥 네스팅 결과 엑셀 다운로드",
                    data=excel_data,
                    file_name=f"{dxf_base_name}_네스팅결과.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width='stretch'
                )

    else:
        st.info("💡 DXF 파일을 업로드하면 패턴 분석이 시작됩니다.")

else:
    # 초기 화면 (파일 업로드 전)
    # 안내 문구
    st.markdown('''
    <div style="text-align: center; padding: 20px; background: #fffbe6; border-radius: 10px; border: 1px solid #ffe58f;">
        <p style="font-size: 16px; color: #d48806; font-weight: bold; margin: 0;">
            ⬆️ 위의 "Browse files" 버튼을 클릭하거나, 그 영역에 DXF 파일을 드래그하세요
        </p>
    </div>
    ''', unsafe_allow_html=True)

    # 50% 크기로 가운데 배치
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("#### 📺 사용 가이드")
        # YouTube 썸네일 + 링크 버튼
        st.image("https://img.youtube.com/vi/Dn_1IsG8J8Q/maxresdefault.jpg", width='stretch')
        st.link_button("▶️ YouTube에서 영상 보기", "https://youtu.be/Dn_1IsG8J8Q", width='stretch')