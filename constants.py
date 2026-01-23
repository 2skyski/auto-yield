# -*- coding: utf-8 -*-
"""
상수 정의 모듈
- 색상, 원단 매핑, 사이즈 순서 등 하드코딩된 값들을 중앙 관리
"""

# ==============================================================================
# 원단 관련 상수
# ==============================================================================

# 원단명 매핑 (DXF CATEGORY/ANNOTATION → 표준 원단명)
FABRIC_MAP = {
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

# 원단별 색상 (Tableau 팔레트 기반)
FABRIC_COLORS = {
    "겉감": "#4c78a8",    # Blue
    "안감": "#e45756",    # Red
    "심지": "#edc948",    # Yellow
    "배색": "#f58518",    # Orange
    "주머니": "#54a24b",  # Green
    "메쉬": "#72b7b2",    # Teal
    "니트": "#9d755d",    # Brown
}
DEFAULT_FABRIC_COLOR = "#dddddd"  # 기본값 (회색)
DEFAULT_FABRIC_NAME = "겉감"

# ==============================================================================
# 사이즈 관련 상수
# ==============================================================================

# 표준 의류 사이즈 정렬 순서 (작은 것 → 큰 것)
SIZE_ORDER = {
    'XS': 10, 'S': 20, 'M': 30, 'L': 40,
    'XL': 50, '2XL': 60, 'XXL': 60,
    '3XL': 70, 'XXXL': 70,
    '4XL': 80, '5XL': 90, '6XL': 100,
    '0X': 5, '1X': 15, '2X': 25, '3X': 35,  # 특수 사이즈
}

# 사이즈 순서 리스트 (레거시 호환)
SIZE_ORDER_LIST = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '2XL', '3XL', '0X', '1X', '2X', '3X']

# ==============================================================================
# DXF 파싱 관련 상수
# ==============================================================================

# 최소 패턴 면적 (이 값 미만은 무시)
MIN_PATTERN_AREA = 1  # mm² 기준

# 기준사이즈 필드 prefix 목록
BASE_SIZE_PREFIXES = [
    'BASE_SIZE:', 'BASESIZE:', 'BASE SIZE:', 'BASE:',
    'REF_SIZE:', 'REFSIZE:', 'REF SIZE:',
    'SAMPLE_SIZE:', 'SAMPLESIZE:', 'SAMPLE SIZE:',
    'MASTER_SIZE:', 'MASTERSIZE:', 'MASTER SIZE:'  # TIIP 형식
]

# 그레인라인 레이어 키워드
GRAINLINE_KEYWORDS = ['GRAIN', 'GL', 'GRAINLINE', '결', '결방향', 'STRAIGHT']
GRAINLINE_LAYER_NUMBERS = ['7']  # 숫자 레이어도 그레인라인일 수 있음

# 단위 변환 스케일
UNIT_SCALE_INCH_TO_MM = 25.4

# ==============================================================================
# UI 관련 상수
# ==============================================================================

# 주요 UI 색상
UI_PRIMARY_COLOR = "#0068c9"
UI_DANGER_COLOR = "#d94a4a"
UI_WARNING_COLOR = "#d48806"
UI_SUCCESS_COLOR = "#27ae60"

# 시트 배경색 (네스팅 시각화)
SHEET_BACKGROUND_COLOR = "#e8f5e9"

# ==============================================================================
# 헬퍼 함수
# ==============================================================================

def get_fabric_color(fabric_name: str) -> str:
    """원단 이름에 따른 색상 코드를 반환합니다."""
    for key, color in FABRIC_COLORS.items():
        if key in fabric_name:
            return color
    return DEFAULT_FABRIC_COLOR


def size_sort_key(size: str) -> tuple:
    """
    사이즈를 작은 것부터 큰 것 순으로 정렬하기 위한 키 함수

    Args:
        size: 사이즈 문자열 (예: 'M', 'XL', '95', '2XL')

    Returns:
        정렬용 튜플 (우선순위, 값)
    """
    if not size:
        return (999, '')

    size_upper = size.upper()

    # 1순위: 표준 사이즈 순서
    if size_upper in SIZE_ORDER:
        return (0, SIZE_ORDER[size_upper])

    # 2순위: 숫자 사이즈 (85, 90, 95, 100, 105 등)
    if size.isdigit():
        return (0, int(size))

    # 3순위: 0X, 2X, 4X 등 (아동/특수 사이즈)
    if size_upper.endswith('X') and size_upper[:-1].isdigit():
        return (0, int(size_upper[:-1]))

    # 4순위: 기타 (알파벳 순)
    return (1, size_upper)


def get_fabric_name(raw_name: str) -> str:
    """
    원단명을 표준 이름으로 매핑합니다.

    Args:
        raw_name: DXF에서 추출한 원단명

    Returns:
        표준 원단명
    """
    if not raw_name:
        return DEFAULT_FABRIC_NAME

    raw_upper = raw_name.upper()
    for key, mapped in FABRIC_MAP.items():
        if key.upper() == raw_upper or key == raw_name:
            return mapped

    return DEFAULT_FABRIC_NAME
