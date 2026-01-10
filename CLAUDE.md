# 스마트 의류 요척 산출서 (Smart Fabric Yield Calculator)

## 프로젝트 개요

DXF 패턴 파일을 분석하여 원단 소요량(요척)을 자동 산출하는 Streamlit 웹 애플리케이션

## 파일 구조

```
D:\CAD\auto-yield\
├── app.py              # 메인 Streamlit 애플리케이션
├── main.py             # CLI 버전 (레거시)
├── requirements.txt    # Python 패키지 의존성
├── README.md           # 사용자 가이드
├── CLAUDE.md           # 개발자 문서 (현재 파일)
└── DXF/                # 테스트용 DXF 파일들
    ├── 35717요척.dxf
    ├── 5535-731.dxf
    └── ...
```

## 기술 스택

- **Frontend**: Streamlit (반응형 웹 UI)
- **DXF 파싱**: ezdxf (CP949 한글 인코딩 지원)
- **형상 분석**: Shapely (폴리곤, 대칭 판별)
- **시각화**: Matplotlib (썸네일), Plotly (인터랙티브 뷰어)
- **데이터**: Pandas, OpenPyXL (엑셀 내보내기)

## 핵심 기능

### 1. DXF 패턴 추출 (`process_dxf`)

**블록 기반 추출 방식** (YUKA CAD 호환)
- INSERT 엔티티 → 블록 정의에서 POLYLINE/LWPOLYLINE 추출
- 각 블록 = 하나의 패턴 (중복 제거 불필요)
- 최소 면적 필터: 30cm² 이상

**추출 정보:**
- `Polygon`: 패턴 형상
- `pattern_name`: 부위명 (ANNOTATION 또는 PIECE NAME에서 추출)
- `fabric_name`: 원단명 (CATEGORY 또는 ANNOTATION에서 추출)

### 2. 원단명 자동 추출

**우선순위:**
1. CATEGORY 필드: `겉감`, `안감`, `메쉬`, `니트` 등
2. ANNOTATION 필드: `LINING` → `안감` 매핑
3. 기본값: `겉감`

**원단명 매핑:**
```python
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
```

### 3. 패턴 이름 추출 (ANNOTATION 필터링)

**제외 대상:**
- 사이즈 호칭: `<S>`, `<M>`, `<L>` 등
- 스타일 번호: `S/#...`, `M/#...`
- 숫자만: `130`, `80` (사이즈)
- 숫자로 시작: `35717요척` (스타일명)
- 원단명: `LINING`, `SHELL` 등
- 배색 관련: `배색` 포함 텍스트

**우선순위:** 한글 부위명 > 영문 부위명

### 4. 수량 자동 결정 로직

대칭 판별 (`check_symmetry`) 기반:

```python
# 1순위: 좌우대칭 + 가로>=25cm + 세로<=15cm → 2장 (COLLAR)
# 2순위: 대칭 + 가로<=25cm + 세로<=25cm → 4장 (FLAP)
# 3순위: 대칭 → 1장 (BODY/SLEEVE)
# 4순위: 비대칭 → 2장 (COLLAR)
```

### 5. 요척 계산 공식

```python
total_area_m2 = sum(패턴면적 × 수량)
fabric_width_m = 원단폭(cm) / 100
efficiency = (100 - 로스율) / 100
required_length_m = total_area_m2 / fabric_width_m / efficiency
required_length_yd = required_length_m * 1.09361
```

### 6. 엑셀 내보내기

**시트 구성:**
- `상세리스트`: 파일명, 스타일번호, 번호, 원단, 구분, 수량, 가로, 세로, 면적
- `요척결과`: 파일명, 스타일번호, 원단, 폭, 단위, 효율(%), 소요량(YD)

## UI 구성

### 상단
- 제목: `👕 스마트 의류 요척 산출서` (1.8rem)
- DXF 파일 업로더

### 일괄 수정 도구
- 전체 선택/해제
- 복사: 선택 패턴 복제 (원단명에 "복사_" 접두어)
- 삭제: 선택 패턴 삭제 (번호 자동 재정렬)
- 원단명 일괄 변경 (텍스트 입력)
- 수량 일괄 변경 (number input)

### 상세 리스트 (data_editor)
- 높이: 735px (20행 표시)
- 컬럼: 형상(썸네일), 번호, 원단, 구분, 수량, 가로, 세로, 면적

### 요척 결과 카드
- 원단별 그룹핑
- 입력: 원단폭(cm), 로스율(%)
- 출력: 소요량(YD)

### 하단
- 엑셀 다운로드 버튼

## 스타일 가이드

### 원단별 색상
```python
fabric_colors = {
    "겉감": "#4a90d9",    # 파랑
    "안감": "#d94a4a",    # 빨강
    "심지": "#9b59b6",    # 보라
    "메쉬": "#27ae60",    # 초록
    "니트": "#f39c12",    # 주황
    "기타": "#7f8c8d",    # 회색
}
```

## 개발 히스토리

### 주요 변경사항
1. **패턴 추출 방식 변경**: polygonize → 블록 기반 (INSERT)
   - 이유: 중복 제거 로직으로 패턴 누락 발생
   - 결과: 13개 → 26개 패턴 정상 인식

2. **원단명 자동 추출 추가**
   - CATEGORY 필드 파싱
   - ANNOTATION의 LINING 키워드 감지

3. **엑셀 내보내기 개선**
   - 파일명, 스타일번호 추가
   - 로스(%) → 효율(%) 변경

4. **UI 개선**
   - 제목 크기 조정 (잘림 방지)
   - 데이터 에디터 높이 735px

5. **패턴 복사/삭제 기능 추가** (2026-01-09)
   - 📋복사: 선택 패턴을 복제하여 다른 원단에 사용 가능
   - 🗑삭제: 선택 패턴 삭제 및 번호 재정렬
   - 수량 입력 버튼 크기 확대 (클릭 용이성 개선)

## 배포

### 로컬 실행
```bash
cd D:\CAD\auto-yield
streamlit run app.py
```

### 서버 배포 (DigitalOcean)
1. DNS: 서브도메인 A 레코드 설정
2. 파일 업로드: `/var/www/auto-yield/`
3. 가상환경 + 패키지 설치
4. systemd 서비스 등록 (포트 8502)
5. Nginx 리버스 프록시
6. Let's Encrypt SSL

## 주의사항

- DXF 파일 인코딩: CP949 (한글) 우선 시도
- 최소 Streamlit 버전: 1.34+ (dialog 기능)
- 캐싱: `@st.cache_data` 사용 (파일별 결과 캐싱)

## 관련 프로젝트

- `D:\CAD\cad-sample-management\`: CAD 샘플 관리 시스템
- `D:\CAD\pattern-data-processor\`: 패턴 데이터 처리기
