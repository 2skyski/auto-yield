import ezdxf
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize
import sys
import math

# --- 기본 설정값 ---
FABRIC_WIDTH_CM = 150 
LOSS_PERCENT = 15 
# -----------------

def extract_lines(entity, lines_list):
    dxftype = entity.dxftype()
    if dxftype == 'LINE':
        start, end = entity.dxf.start, entity.dxf.end
        lines_list.append(LineString([(start.x, start.y), (end.x, end.y)]))
    elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
        try:
            points = list(entity.points())
            if len(points) > 1:
                lines_list.append(LineString([(p[0], p[1]) for p in points]))
        except: pass
    elif dxftype in ['SPLINE', 'ARC', 'CIRCLE', 'ELLIPSE']:
        try:
            path = ezdxf.path.make_path(entity)
            vertices = list(path.flattening(distance=1.0))
            if len(vertices) > 1:
                lines_list.append(LineString([(v.x, v.y) for v in vertices]))
        except: pass
    elif dxftype == 'INSERT':
        try:
            for virtual_entity in entity.virtual_entities():
                extract_lines(virtual_entity, lines_list)
        except: pass

def check_is_fold_pattern(poly):
    """
    패턴이 '골(Fold)'인지 추측하는 함수
    원리: 패턴의 외곽선 중, 전체 높이(또는 너비)의 95% 이상을 차지하는
    '완벽한 직선'이 있다면 골 패턴일 확률이 높음.
    """
    minx, miny, maxx, maxy = poly.bounds
    full_height = maxy - miny
    full_width = maxx - minx
    
    # 외곽선 좌표 가져오기
    coords = list(poly.exterior.coords)
    
    for i in range(len(coords)-1):
        p1 = coords[i]
        p2 = coords[i+1]
        
        # 선분 길이 계산
        seg_len = math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
        
        # 1. 수직선인지 확인 (X좌표 차이가 거의 없음)
        if abs(p1[0] - p2[0]) < 0.1: 
            # 그 수직선이 전체 높이의 90% 이상인가? (앞/뒤 중심선일 확률 높음)
            if seg_len > (full_height * 0.9):
                return True, "수직골"

        # 2. 수평선인지 확인 (Y좌표 차이가 거의 없음)
        if abs(p1[1] - p2[1]) < 0.1:
            # 그 수평선이 전체 너비의 90% 이상인가?
            if seg_len > (full_width * 0.9):
                return True, "수평골"
                
    return False, ""

def calculate_yield(dxf_file):
    print(f"\n=== 스마트 골(Fold) 감지 모드: {dxf_file} ===")
    
    try:
        doc = ezdxf.readfile(dxf_file)
        msp = doc.modelspace()
    except:
        print("파일 열기 실패")
        return

    lines_list = []
    for entity in msp:
        extract_lines(entity, lines_list)

    try:
        raw_polygons = list(polygonize(lines_list))
    except:
        return
    
    # 1차 필터
    candidates = []
    for poly in raw_polygons:
        if (poly.area / 100) > 50: 
            candidates.append(poly)

    candidates.sort(key=lambda x: x.area, reverse=True)

    final_patterns = []
    
    # 2차 필터
    for poly in candidates:
        is_duplicate = False
        poly_center = poly.centroid
        for existing in final_patterns:
            if poly_center.distance(existing.centroid) < 50: 
                is_duplicate = True
                break
        if not is_duplicate:
            final_patterns.append(poly)

    # 3. 패턴 분석 및 수량 자동 추천
    pattern_data = []

    print("\n[패턴 분석표]")
    print(f"{'No':<3} | {'가로':<6} | {'세로':<6} | {'타입추측':<10} | {'수량':<4}")
    print("-" * 55)

    for i, poly in enumerate(final_patterns):
        minx, miny, maxx, maxy = poly.bounds
        width_cm = (maxx - minx) / 10
        height_cm = (maxy - miny) / 10
        
        # 골(Fold) 감지 로직 실행
        is_fold, reason = check_is_fold_pattern(poly)
        
        max_len = max(width_cm, height_cm)
        
        # --- 수량 결정 로직 ---
        if max_len >= 25: # 큰 패턴(몸판 등)
            if is_fold:
                size_type = f"대/{reason}" # 예: 대/수직골
                default_count = 1  # 골이니까 1장
            else:
                size_type = "대/일반"
                default_count = 2  # 일반이니까 2장
        else: # 작은 패턴(부속)
            # 작은건 골이어도 펼쳐서 재단하는 경우가 많으므로 일단 사용자가 보게 둠
            size_type = "소(부속)"
            default_count = 2 
            
        print(f"{i+1:02d}  | {width_cm:6.1f} | {height_cm:6.1f} | {size_type:<10} | {default_count}개")
        
        pattern_data.append({
            'poly': poly,
            'count': default_count
        })

    print("-" * 55)
    print("📢 '타입추측'에 '수직골' 등이 뜨면 자동으로 1개로 설정했습니다.")
    print("   - 그래도 틀린 게 있다면 '번호=수량'을 입력해 수정하세요.")
    print("   - 맞으면 그냥 엔터(Enter)를 누르세요.")
    
    # 4. 수정 및 계산
    while True:
        user_input = input("\n👉 수정 입력 (엔터로 완료): ")
        if not user_input.strip():
            break
        try:
            changes = user_input.split()
            for change in changes:
                idx_str, count_str = change.split('=')
                idx = int(idx_str) - 1
                pattern_data[idx]['count'] = int(count_str)
                print(f"   ✔ {idx+1}번 -> {int(count_str)}개로 변경")
        except:
            print("   ⚠ 입력 오류 (예: 1=1 5=4)")

    total_area_m2 = 0
    total_pieces = 0

    for p in pattern_data:
        total_area_m2 += (p['poly'].area / 1_000_000) * p['count']
        total_pieces += p['count']

    fabric_width_m = FABRIC_WIDTH_CM / 100
    efficiency = (100 - LOSS_PERCENT) / 100
    required_length_yd = ((total_area_m2 / fabric_width_m) / efficiency) * 1.09361

    print("-" * 40)
    print(f"▶ 총 패턴 수: {total_pieces}개")
    print(f"▶ 총 면적: {total_area_m2:.4f} m²")
    print("-" * 40)
    print(f"★ 최종 예상 소요량: {required_length_yd:.2f} YD")
    print("-" * 40)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        calculate_yield(sys.argv[1])
    else:
        print("사용법: python main.py 파일명.dxf")