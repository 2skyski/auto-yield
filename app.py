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

    /* 수량 입력 플러스/마이너스 버튼 크기 확대 */
    div[data-testid="stNumberInput"] button {
        width: 40px !important;
        min-width: 40px !important;
        padding: 0 10px !important;
    }
    div[data-testid="stNumberInput"] button svg {
        width: 20px !important;
        height: 20px !important;
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


def check_horizontal_edges(poly):
    """
    패턴의 가로변(상단/하단)이 직선이거나 평행선인지 판별합니다.
    조건1: 상단 또는 하단 중 한 변이 직선 (Y좌표 변화 1% 이내)
    조건2: 상하단이 평행선 (가로 길이 비율 60% 이상)
    """
    try:
        coords = list(poly.exterior.coords)
        if len(coords) < 4:
            return False, "점 부족"

        minx, miny, maxx, maxy = poly.bounds
        height = maxy - miny
        width = maxx - minx

        # 상단/하단 영역 정의 (전체 높이의 10% 이내)
        top_threshold = maxy - height * 0.1
        bottom_threshold = miny + height * 0.1

        # 상단/하단에 위치한 점들 추출
        top_points = [(x, y) for x, y in coords if y >= top_threshold]
        bottom_points = [(x, y) for x, y in coords if y <= bottom_threshold]

        def is_straight_line(points, tolerance_ratio=0.01):
            """점들이 직선(수평선)인지 판별 (Y좌표 변화가 거의 없음)"""
            if len(points) < 2:
                return False
            y_values = [p[1] for p in points]
            y_range = max(y_values) - min(y_values)
            return y_range < height * tolerance_ratio

        def get_edge_length(points):
            """점들의 X방향 너비 (가로 길이)"""
            if len(points) < 2:
                return 0
            x_values = [p[0] for p in points]
            return max(x_values) - min(x_values)

        # 조건1: 상단 또는 하단이 직선인지 확인
        top_is_straight = is_straight_line(top_points)
        bottom_is_straight = is_straight_line(bottom_points)

        if top_is_straight or bottom_is_straight:
            return True, "직선변"

        # 조건2: 상하단이 평행선인지 확인 (길이 비율 60% 이상)
        top_length = get_edge_length(top_points)
        bottom_length = get_edge_length(bottom_points)

        if top_length > 0 and bottom_length > 0:
            similarity = min(top_length, bottom_length) / max(top_length, bottom_length)
            if similarity >= 0.6:
                return True, "평행선"

        return False, "해당없음"
    except:
        return False, "오류"


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
                        # 스타일번호: S/#..., M/#... 형식
                        if text.startswith('ANNOTATION:') and '/#' in text:
                            val = text.replace('ANNOTATION:', '').strip()
                            # S/#5535-731 → 5535-731
                            if '/#' in val:
                                return val.split('/#')[1]
                break  # 첫 번째 블록에서만 추출
    except:
        pass
    return ""


@st.cache_data
def process_dxf(file_path):
    """
    DXF 파일을 읽어 (Polygon, 패턴이름, 원단명) 튜플 리스트를 반환합니다.
    블록(INSERT) 기반으로 처리하여 패턴 누락을 방지합니다.
    """
    try:
        # 한글 인코딩(CP949) 우선 시도
        try:
            doc = ezdxf.readfile(file_path, encoding='cp949')
        except:
            doc = ezdxf.readfile(file_path)

        msp = doc.modelspace()

        final = []

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

                    # 블록 내 가장 큰 닫힌 POLYLINE 선택 + 텍스트 추출
                    for be in block:
                        if be.dxftype() == 'POLYLINE' and be.is_closed:
                            pts = list(be.points())
                            if len(pts) >= 3:
                                coords = [(p[0], p[1]) for p in pts]
                                poly = Polygon(coords)
                                if poly.is_valid and poly.area > max_area:
                                    max_area = poly.area
                                    max_poly = poly
                        elif be.dxftype() == 'LWPOLYLINE' and be.closed:
                            pts = list(be.points())
                            if len(pts) >= 3:
                                coords = [(p[0], p[1]) for p in pts]
                                poly = Polygon(coords)
                                if poly.is_valid and poly.area > max_area:
                                    max_area = poly.area
                                    max_poly = poly
                        elif be.dxftype() == 'TEXT':
                            text = be.dxf.text

                            # CATEGORY 필드에서 원단명 추출
                            if text.startswith('CATEGORY:'):
                                cat_val = text.replace('CATEGORY:', '').strip()
                                if cat_val:
                                    # 매핑된 원단명 찾기
                                    for key, mapped in fabric_map.items():
                                        if key.upper() == cat_val.upper() or key == cat_val:
                                            fabric_name = mapped
                                            break
                                    # 매핑 안 되면 원본 사용
                                    if not fabric_name and cat_val:
                                        fabric_name = cat_val

                            # ANNOTATION 필드 처리
                            elif text.startswith('ANNOTATION:'):
                                val = text.replace('ANNOTATION:', '').strip()
                                if not val:
                                    continue

                                # ANNOTATION에서 원단명 키워드 체크 (LINING 등)
                                val_upper = val.upper()
                                if val_upper in fabric_map:
                                    if not fabric_name:  # CATEGORY가 없을 때만
                                        fabric_name = fabric_map[val_upper]
                                    continue

                                # 제외 대상 체크
                                # 1. 사이즈 호칭: <S>, <M>, <L> 등
                                if val.startswith('<'):
                                    continue
                                # 2. 스타일 번호: S/#..., M/#... 등
                                if val.startswith(('S/', 'M/', 'L/', '#')):
                                    continue
                                # 3. 숫자만 (사이즈: 130, 80 등)
                                if val.isdigit():
                                    continue
                                # 4. 숫자로 시작 (스타일명: 35717요척 등)
                                if val[0].isdigit():
                                    continue
                                # 5. 원단명 (이미 위에서 처리됨)
                                fabric_keywords = ['LINING', 'SHELL', 'INTERLINING', '안감', '겉감', '심지']
                                if val.upper() in [f.upper() for f in fabric_keywords]:
                                    continue
                                # 6. 배색 관련
                                if '배색' in val:
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

                    # 30cm² 이상인 패턴만 추가
                    if max_poly and (max_area / 100) > 30:
                        final.append((max_poly, pattern_name, fabric_name))
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

            candidates = [p for p in raw_polys if (p.area / 100) > 30]
            candidates.sort(key=lambda x: x.area, reverse=True)

            # 레거시 방식에서만 중복 제거 (패턴 이름/원단명 없음 → 기본값)
            added_polys = []
            for p in candidates:
                if not any(p.centroid.distance(e.centroid) < 50 for e in added_polys):
                    added_polys.append(p)
                    final.append((p, "", "겉감"))  # 원단명 기본값: 겉감

        # 면적 기준 정렬 (큰 것부터)
        final.sort(key=lambda x: x[0].area, reverse=True)
        return final

    except Exception as e:
        return []


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
    st.plotly_chart(fig, use_container_width=True)
    
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

# A. 파일 업로드 섹션
uploaded_file = st.file_uploader("DXF 파일을 업로드하세요 (YUKA, Optitex 등)", type=["dxf"])

if uploaded_file is not None:
    # 최초 로드시 패턴 분석 실행
    if st.session_state.patterns is None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_path = tmp_file.name
        
        patterns = process_dxf(tmp_path)
        style_no = extract_style_no(tmp_path)
        os.remove(tmp_path) # 임시 파일 삭제
        st.session_state.patterns = patterns
        st.session_state.style_no = style_no
        
        # 초기 데이터프레임 생성
        data_list = []
        for i, (poly, pattern_name, fabric_name) in enumerate(patterns):
            minx, miny, maxx, maxy = poly.bounds
            w, h = (maxx - minx) / 10, (maxy - miny) / 10

            # 대칭 여부 확인
            is_symmetric, sym_reason = check_symmetry(poly)

            # DXF에서 추출한 원단명 사용 (없으면 겉감)
            extracted_fabric = fabric_name if fabric_name else "겉감"

            # 가로변 직선/평행선 여부 확인
            has_straight_edge, edge_reason = check_horizontal_edges(poly)

            # 수량 결정 우선순위:
            # 1순위: 좌우대칭 + 가로>=35cm + 세로<=15cm + (직선변 또는 평행선) → 1장 (BODY)
            # 2순위: 좌우대칭 + 가로>=25cm + 세로<=15cm → 2장 (부속)
            # 3순위: 대칭 + 가로<=25cm + 세로<=25cm → 4장 (FLAP)
            # 4순위: 대칭이면 1장 (BODY)
            # 5순위: 비대칭이면 2장 (부속)

            if is_symmetric and sym_reason == "좌우대칭" and w >= 35 and h <= 15 and has_straight_edge:
                count = 1
                default_desc = "BODY"
            elif is_symmetric and sym_reason == "좌우대칭" and w >= 25 and h <= 15:
                count = 2
                default_desc = "부속"
            elif is_symmetric and w <= 25 and h <= 25:
                count = 4
                default_desc = "FLAP"
            elif is_symmetric:
                count = 1
                default_desc = "BODY"
            else:
                count = 2
                default_desc = "부속"

            # DXF 텍스트에서 패턴 이름이 있으면 사용, 없으면 기본값
            desc = pattern_name if pattern_name else default_desc

            data_list.append({
                "형상": poly_to_base64(poly, get_fabric_color_hex(extracted_fabric)), # DXF 원단명으로 색상 적용
                "번호": i+1, "원단": extracted_fabric, "구분": desc, "수량": count,
                "가로(cm)": round(w, 1), "세로(cm)": round(h, 1), "면적_raw": poly.area / 1000000
            })
        st.session_state.df = pd.DataFrame(data_list)
        # 체크박스 상태 초기화
        for i in range(len(patterns)): st.session_state[f"chk_{i}"] = False

    # 데이터 로드
    patterns = st.session_state.patterns
    df = st.session_state.df

    if patterns:
        # 썸네일 비율 고정용 Max값 계산
        max_dim = 0
        for p, _, _ in patterns:  # (poly, pattern_name, fabric_name)
            minx, miny, maxx, maxy = p.bounds
            max_dim = max(max_dim, maxx - minx, maxy - miny)
        zoom_span = max_dim * 1.1 

        # ----------------------------------------------------------------
        # B. 일괄 수정 도구 (Batch Edit Tools)
        # ----------------------------------------------------------------
        st.markdown("#### ✨ 일괄 수정 도구")
        tool_col1, tool_col2, tool_col3 = st.columns([1.5, 1.5, 2])
        
        # 1. 전체 선택/해제/복사/삭제
        with tool_col1:
            c1, c2, c3, c4 = st.columns(4)
            if c1.button("✅전체", use_container_width=True, help="모든 패턴 선택"):
                for i in range(len(patterns)): st.session_state[f"chk_{i}"] = True
                st.rerun()
            if c2.button("⬜해제", use_container_width=True, help="모든 선택 해제"):
                for i in range(len(patterns)): st.session_state[f"chk_{i}"] = False
                st.rerun()
            if c3.button("📋복사", use_container_width=True, help="선택 패턴 복사"):
                sel_indices = [i for i in range(len(patterns)) if st.session_state.get(f"chk_{i}")]
                if sel_indices:
                    # 현재 데이터 복사
                    new_patterns = list(st.session_state.patterns)
                    new_df = st.session_state.df.copy()

                    for idx in sel_indices:
                        # 패턴 복제 (poly, pattern_name, fabric_name)
                        orig_pattern = st.session_state.patterns[idx]
                        new_patterns.append(orig_pattern)

                        # 데이터프레임 행 복제
                        new_row = new_df.iloc[idx].copy()
                        new_row["번호"] = len(new_patterns)  # 새 번호 부여
                        new_row["원단"] = "복사_" + new_row["원단"]  # 복사 표시
                        # 썸네일 색상 업데이트
                        new_row["형상"] = poly_to_base64(orig_pattern[0], get_fabric_color_hex(new_row["원단"]))
                        new_df = pd.concat([new_df, pd.DataFrame([new_row])], ignore_index=True)

                    # 세션 상태 업데이트
                    st.session_state.patterns = new_patterns
                    st.session_state.df = new_df

                    # 새 패턴들의 체크박스 초기화
                    for i in range(len(patterns), len(new_patterns)):
                        st.session_state[f"chk_{i}"] = False

                    # 기존 선택 해제
                    for i in sel_indices:
                        st.session_state[f"chk_{i}"] = False

                    st.rerun()
            if c4.button("🗑삭제", use_container_width=True, help="선택 패턴 삭제"):
                sel_indices = [i for i in range(len(patterns)) if st.session_state.get(f"chk_{i}")]
                if sel_indices:
                    # 선택되지 않은 패턴만 유지
                    keep_indices = [i for i in range(len(patterns)) if i not in sel_indices]

                    # 패턴 리스트 필터링
                    new_patterns = [st.session_state.patterns[i] for i in keep_indices]

                    # 데이터프레임 필터링 및 번호 재정렬
                    new_df = st.session_state.df.iloc[keep_indices].copy()
                    new_df = new_df.reset_index(drop=True)
                    new_df["번호"] = range(1, len(new_df) + 1)

                    # 세션 상태 업데이트
                    st.session_state.patterns = new_patterns
                    st.session_state.df = new_df

                    # 체크박스 상태 초기화
                    for key in list(st.session_state.keys()):
                        if key.startswith("chk_"):
                            del st.session_state[key]
                    for i in range(len(new_patterns)):
                        st.session_state[f"chk_{i}"] = False

                    st.rerun()

        # 2. 원단명 변경
        with tool_col2:
            f1, f2 = st.columns([3, 1])
            new_fabric = f1.text_input("원단명", placeholder="예: 안감", label_visibility="collapsed")
            if f2.button("원단적용", use_container_width=True):
                sel_indices = [i for i in range(len(patterns)) if st.session_state.get(f"chk_{i}")]
                if sel_indices and new_fabric:
                    new_color = get_fabric_color_hex(new_fabric)
                    for idx in sel_indices: 
                        st.session_state.df.at[idx, "원단"] = new_fabric
                        # 썸네일 색상 업데이트
                        st.session_state.df.at[idx, "형상"] = poly_to_base64(patterns[idx][0], new_color)
                    st.rerun()
        
        # 3. 수량 변경
        with tool_col3:
            n1, n2 = st.columns([3, 1])
            new_count = n1.number_input("수량", min_value=0, label_visibility="collapsed")
            if n2.button("수량적용", use_container_width=True):
                sel_indices = [i for i in range(len(patterns)) if st.session_state.get(f"chk_{i}")]
                if sel_indices:
                    for idx in sel_indices: st.session_state.df.at[idx, "수량"] = new_count
                    st.rerun()

        st.divider()

        # ----------------------------------------------------------------
        # C. 썸네일 그리드 (20 Columns Grid)
        # ----------------------------------------------------------------
        st.caption("💡 썸네일 아래 **[숫자 버튼]**을 누르면 확대 창이 열립니다.")
        
        cols_per_row = 20
        rows = math.ceil(len(patterns) / cols_per_row)
        
        for row in range(rows):
            cols = st.columns(cols_per_row)
            for col_idx in range(cols_per_row):
                idx = row * cols_per_row + col_idx
                if idx < len(patterns):
                    with cols[col_idx]:
                        p = patterns[idx][0]
                        current_fabric = df.at[idx, "원단"]
                        
                        # Matplotlib 썸네일 생성 (가볍고 빠름)
                        fig, ax = plt.subplots(figsize=(1, 1)) 
                        x, y = p.exterior.xy
                        ax.plot(x, y, 'k-', lw=0.5)
                        ax.fill(x, y, color=get_fabric_color_hex(current_fabric), alpha=0.6)
                        # 비율 고정 및 축 숨김
                        ax.set_xlim(p.centroid.x - zoom_span/2, p.centroid.x + zoom_span/2)
                        ax.set_ylim(p.centroid.y - zoom_span/2, p.centroid.y + zoom_span/2)
                        ax.set_aspect('equal'); ax.axis('off')
                        st.pyplot(fig, use_container_width=True)
                        plt.close(fig) # 메모리 해제
                        
                        # 팝업 호출 버튼
                        if st.button(f"{idx+1}", key=f"btn_zoom_{idx}", use_container_width=True):
                            show_detail_viewer(idx, p, current_fabric)
                        
                        # 선택 체크박스
                        st.checkbox("선택", key=f"chk_{idx}", label_visibility="collapsed")

        st.divider()

        # ----------------------------------------------------------------
        # D. 하단 작업창: 리스트 & 요척 결과 (Results)
        # ----------------------------------------------------------------
        col1, col2 = st.columns([3, 2])
        
        # [왼쪽] 상세 리스트 (Data Editor)
        with col1:
            st.markdown("#### 📝 상세 리스트")
            # 내부 계산용 컬럼은 숨기고 표시
            display_df = st.session_state.df.copy()
            # 면적(raw)는 m² 단위이므로, cm²로 변환하려면 * 10000
            display_df["면적(cm²)"] = (display_df["면적_raw"] * 10000).round(1) 
            display_df = display_df.drop(columns=["면적_raw"])
            
            edited_df = st.data_editor(
                display_df,
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                disabled=["면적(cm²)", "형상"], # 수정 불가 컬럼
                column_config={
                    "형상": st.column_config.ImageColumn(
                        "형상", help="패턴 미리보기", width="small"
                    )
                },
                height=735,  # 20개 행 표시 (행당 약 35px + 헤더)
                key="editor"
            )
            
            # 변경사항 감지 및 데이터 업데이트
            if edited_df is not None:
                # 원단명이 변경되었는지 확인하여 썸네일 업데이트
                is_changed = False
                for i in range(len(edited_df)):
                    old_fabric = st.session_state.df.at[i, "원단"]
                    new_fabric = edited_df.at[i, "원단"]
                    
                    # 1. 원단명 변경 시 썸네일 재생성
                    if old_fabric != new_fabric:
                        new_color = get_fabric_color_hex(new_fabric)
                        edited_df.at[i, "형상"] = poly_to_base64(patterns[i][0], new_color)
                        is_changed = True
                    
                    # 2. 다른 데이터 업데이트 (수량 등)
                    if st.session_state.df.at[i, "수량"] != edited_df.at[i, "수량"]:
                        is_changed = True

                if is_changed:
                    # 면적 컬럼 등을 제외한 원본 데이터 구조로 다시 복원하여 저장
                    # (현재 edited_df에는 '면적(cm²)'가 있고 '면적_raw'가 없음)
                    
                    # 기존 면적_raw 유지
                    edited_df["면적_raw"] = st.session_state.df["면적_raw"] 
                    # 계산된 컬럼 제거
                    if "면적(cm²)" in edited_df.columns:
                        edited_df = edited_df.drop(columns=["면적(cm²)"])
                        
                    st.session_state.df = edited_df
                    st.rerun()
            
        # [오른쪽] 요척 결과 카드 (Compact View)
        with col2:
            st.markdown("#### 📊 요척 결과")
            
            # 헤더 라벨
            h1, h2, h_u, h3, h4 = st.columns([1.4, 0.9, 0.9, 0.9, 1.4]) # 컬럼 비율 조정 (단위 +0.1, 요척 -0.1)
            h1.caption("원단명")
            h2.caption("폭(W)")
            h_u.caption("단위")
            h3.caption("로스(%)")
            h4.caption("필요요척(YD)")

            # 데이터 재계산
            calc_df = edited_df.copy()
            calc_df["면적_raw"] = st.session_state.df["면적_raw"]
            grouped = calc_df.groupby("원단")
            
            for i, (fabric_name, group) in enumerate(grouped):
                with st.container(border=True):
                    # 한 줄(Row) 레이아웃 적용
                    c1, c2, c_unit, c3, c4 = st.columns([1.4, 0.9, 0.9, 0.9, 1.4])
                    
                    with c1: # 원단명 뱃지
                        color = get_fabric_color_hex(fabric_name)
                        st.markdown(f"""
                        <div style='background-color:{color}; padding:5px 0px; border-radius:4px; text-align:center;'>
                            <strong style='font-size:14px; color:#333;'>{fabric_name}</strong>
                        </div>""", unsafe_allow_html=True)
                    
                    with c2: # 폭 입력
                        input_width = st.number_input("W", value=58.00, min_value=10.0, step=0.1, format="%.2f", key=f"w{i}", label_visibility="collapsed")
                        
                    with c_unit: # 단위 선택 (cm/in)
                        unit = st.selectbox("U", ["in", "cm"], key=f"unit{i}", label_visibility="collapsed")
                    
                    with c3: # 로스 입력
                        input_loss = st.number_input("L", value=15, min_value=0, key=f"l{i}", label_visibility="collapsed")
                    
                    with c4: # 결과 계산 및 표시
                        group_area = sum(row['면적_raw'] * row['수량'] for _, row in group.iterrows())
                        
                        if input_width > 0:
                            # 1. 폭을 미터(m) 단위로 환산
                            if unit == "cm":
                                width_m = input_width / 100
                            else: # in
                                width_m = (input_width * 2.54) / 100
                                
                            # 2. 공식: (총면적으로 필요한 길이(m) / 효율) * 야드환산계수
                            # 필요한 길이(m) = 총면적(m²) / 폭(m)
                            req_yd = ((group_area / width_m) / ((100-input_loss)/100)) * 1.09361
                        else: req_yd = 0
                        
                        st.markdown(f"""
                        <div style='text-align:right; padding-top:5px;'>
                            <span style='font-size:18px; color:#0068c9; font-weight:bold;'>{req_yd:.2f} YD</span>
                        </div>""", unsafe_allow_html=True)

            # ----------------------------------------------------------------
            # E. 엑셀 다운로드 버튼
            # ----------------------------------------------------------------
            st.divider()

            # 요척 결과 데이터 수집
            yield_data = []
            for i, (fabric_name, group) in enumerate(grouped):
                input_width = st.session_state.get(f"w{i}", 58.0)
                unit = st.session_state.get(f"unit{i}", "in")
                input_loss = st.session_state.get(f"l{i}", 15)
                group_area = sum(row['면적_raw'] * row['수량'] for _, row in group.iterrows())

                if input_width > 0:
                    if unit == "cm":
                        width_m = input_width / 100
                    else:
                        width_m = (input_width * 2.54) / 100
                    req_yd = ((group_area / width_m) / ((100-input_loss)/100)) * 1.09361
                else:
                    req_yd = 0

                yield_data.append({
                    "원단명": fabric_name,
                    "폭": input_width,
                    "단위": unit,
                    "효율(%)": 100 - input_loss,
                    "필요요척(YD)": round(req_yd, 2)
                })

            yield_df = pd.DataFrame(yield_data)

            # 엑셀 파일 생성
            excel_buffer = io.BytesIO()
            file_name = uploaded_file.name.replace('.dxf', '').replace('.DXF', '')
            style_no = st.session_state.get('style_no', '')

            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                # 시트1: 상세리스트 (형상 컬럼 제외)
                detail_df = display_df.drop(columns=["형상"], errors='ignore')
                # 파일명, 스타일번호 컬럼 추가
                detail_df.insert(0, "스타일번호", style_no)
                detail_df.insert(0, "파일명", file_name)
                detail_df.to_excel(writer, sheet_name='상세리스트', index=False)

                # 시트2: 요척결과
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
                use_container_width=True
            )
    else:
        st.info("💡 DXF 파일을 업로드하면 패턴 분석이 시작됩니다.")