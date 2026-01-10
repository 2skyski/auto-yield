# 스마트 의류 요척 산출서 (Smart Fabric Yield Calculator)

DXF 패턴 파일을 분석하여 원단 소요량(요척)을 자동 산출하는 Streamlit 웹 애플리케이션

## 주요 기능

### 1. DXF 패턴 분석
- YUKA CAD 호환 DXF 파일 파싱
- 패턴 형상 자동 추출 및 면적 계산
- 원단명/부위명 자동 인식

### 2. Sparrow 네스팅 (State-of-the-art)
최신 2D 불규칙 패킹 알고리즘을 적용한 고효율 네스팅

| 항목 | 성능 |
|------|------|
| 효율 | 85%+ (상업용 수준) |
| 속도 | 10~30초 |
| 회전 | 0°/180° 지원 |

**사용법:**
1. 네스팅 탭에서 "Sparrow 최적화" 체크 (기본 활성화)
2. 최적화 시간 설정 (30초 권장)
3. "네스팅 실행" 클릭

### 3. 요척 계산
- 원단별 소요량(YD) 자동 계산
- 원단 폭, 로스율 설정 가능
- 엑셀 내보내기 지원

## 설치

### 필수 패키지
```bash
pip install -r requirements.txt
pip install spyrrow  # Sparrow 네스팅 엔진
```

### 실행
```bash
streamlit run app.py
```

## 파일 구조

```
auto-yield/
├── app.py                 # 메인 Streamlit 애플리케이션
├── nesting_engine.py      # 네스팅 엔진 모듈
├── requirements.txt       # Python 패키지 의존성
├── README.md              # 프로젝트 설명서
├── CLAUDE.md              # 개발자 문서
└── DXF/                   # 테스트용 DXF 파일
```

## 기술 스택

- **Frontend**: Streamlit
- **DXF 파싱**: ezdxf
- **형상 분석**: Shapely
- **네스팅**: spyrrow (Sparrow)
- **시각화**: Matplotlib, Plotly
- **데이터**: Pandas, OpenPyXL

## 참고

- [Sparrow](https://github.com/JeroenGar/sparrow) - State-of-the-art 2D 네스팅 알고리즘
- [spyrrow](https://github.com/PaulDL-RS/spyrrow) - Sparrow Python 래퍼
