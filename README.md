# Sub Translation QC

한국어 드라마·영화의 AI 번역 자막(SRT)을 점검하고, 사람이 검수해 최종 SRT를 내보내는 도구입니다. 한국어 원본 영상과 번역 SRT를 입력하며, 한국어 SRT도 함께 사용할 수 있습니다. 현재 지원하는 언어 프로필은 스페인어 LATAM(`es_LATAM`), 영어(`en_US`), 프랑스어(`fr_FR`), 포르투갈어(`pt_BR`)입니다.

대상 언어에 익숙하지 않은 검수자도 판단할 수 있도록 한국어 대사, 교정 이유, 한국어 역번역을 함께 보여줍니다. **Claude와 GPT가 합의한 번역 교정도 자동 승인하지 않고 검수자의 판단을 기다립니다.** 온점·줄 길이 등 형식 보정은 번역 교정과 별도로 처리합니다.

- **백엔드**: FastAPI, 비동기 SQLAlchemy, PostgreSQL, Alembic. 분석은 별도 워커 없이 FastAPI 프로세스 안의 `asyncio` 태스크로 실행합니다.
- **프론트엔드**: React 19, Vite, Tailwind. 작품 아카이브 → 성별·격식 확인 → 검수 화면으로 이동합니다.
- **분석**: Claude·GPT, spaCy·kiwipiepy 형태소 분석, ffmpeg 영상·오디오 처리. 운영은 `LiveModelProvider`, 자동 테스트는 테스트 전용 `MockProvider` 등을 사용합니다.

## 목차

- [사용 흐름](#사용-흐름)
- [분석 파이프라인](#분석-파이프라인)
- [검수와 내보내기](#검수와-내보내기)
- [데이터와 파일 보관](#데이터와-파일-보관)
- [프로젝트 구조](#프로젝트-구조)
- [실행 방법](#실행-방법)
- [배포](#배포)
- [현재 제약](#현재-제약)

## 사용 흐름

```mermaid
flowchart TD
    A["작품·회차·언어판 등록<br/>영상 + 번역 SRT + 선택적 한국어 SRT"]
    B["1단계 분석<br/>대사 정렬 · 전처리 · 성별/격식 판단"]
    C{"미확정 성별/격식이 있는가?"}
    D["검수자가 영상과 대사를 보고 확정"]
    E["2단계 분석<br/>Claude·GPT 독립 검증 · 교정안과 역번역 생성"]
    F["검수<br/>승인 · 직접 수정 · 반려 · 재질의"]
    G["내보내기 점검<br/>최종 줄 길이 · 용어집 일관성 확인"]
    H["최종 SRT 다운로드 · 내보내기 이력 저장"]
    A --> B --> C
    C -->|예| D
    C -->|아니오| E
    D -->|확인 완료 후 AI 검증 시작| E
    E --> F --> G --> H
```

작품 아카이브는 왼쪽 작품 목록과 오른쪽 상세·등록 화면으로 구성됩니다. 영화·시리즈 필터, 작품명·유형 수정, 회차·언어판 추가, 검수 이어가기, 재분석·삭제, 저장 공간 확인을 제공합니다. 작품별 캐릭터 성별과 용어집도 이 화면에서 관리합니다.

미확정 성별·격식이 있으면 확인 화면에서 먼저 답한 뒤 **AI 검증 시작하기**를 누릅니다. 처음부터 모두 확정되어 있으면 이 관문을 건너뛰고 번역 검증으로 진행합니다. 분석 결과와 검수 상태는 DB에 저장되어 나중에 이어서 검수할 수 있습니다.

## 분석 파이프라인

`POST /target-versions/{id}/run-analysis`는 `background.py`의 `analyze_and_save()`를 시작합니다. `core/pipeline.py`의 Phase 1 결과를 저장한 뒤, 확인 필요 여부에 따라 Phase 2를 바로 실행하거나 사람의 확인을 기다립니다.

### Phase 1: 대사 정렬과 성별·격식 확인

1. **입력 준비와 정렬**
   - 한국어 SRT가 있으면 이를 기준으로 OpenAI 임베딩 유사도와 시간 겹침을 이용한 DP 정렬을 수행합니다. 전체 영상 STT는 생략하지만, 영상과 자막의 동기화를 확인하기 위해 앞부분의 짧은 STT를 실행할 수 있습니다.
   - 한국어 SRT가 없으면 같은 회차의 사용 가능한 STT 캐시와 영상 프록시를 재사용합니다. 재사용할 수 없으면 ffmpeg로 오디오를 추출하고 600초 단위로 나눠 GPT STT를 병렬 실행한 뒤 단어 타이밍으로 정렬합니다.
   - 검수용 480p 영상 프록시를 생성하거나 재사용합니다. 한국어 대사만 있거나 번역만 있는 미매칭 줄도 결과에 남깁니다.
2. **규칙 기반 전처리**
   - 연속 온점, CTA 문구, 비속어 사전 등을 처리합니다. 현재 비속어 사전은 비어 있으며, 용어집 전체를 일괄 치환하는 단계는 없습니다.
3. **성별·격식 판단**
   - spaCy로 대상 언어를, kiwipiepy로 한국어 어미·호칭 등을 분석해 후보를 찾고 판단 가능한 값은 결정합니다. 영어 프로필은 격식 확인을 사용하지 않습니다.
   - 미해결 성별 후보는 GPT 경량 모델이 주변 대사를 보고 인물·지칭 대상별 그룹으로 묶어 추정합니다. 확인에 필요한 단어의 한국어 뜻도 생성합니다.
   - 같은 작품에서 저장한 `CharacterGenderFact`는 해당 인물의 성별을 자동으로 채우는 데 사용합니다. 여전히 불확실한 항목만 사람이 확인합니다.
4. **저장과 확인 관문**
   - 결과·캐시·프록시 경로를 DB에 커밋한 뒤 원본 영상 삭제를 시도합니다.
   - 미확정 항목이 있으면 `awaiting_confirmation`, 없으면 `verifying` 상태로 이동합니다.
   - `confirm-registers`는 모든 필수 확인이 끝났는지 검사합니다. 사람이 확정한 이름 있는 인물의 성별은 충돌 여부를 확인해 작품 단위로 저장하고 Phase 2를 시작합니다.

### Phase 2: 번역 검증과 교정안 생성

```mermaid
flowchart TD
    A["확정된 성별·격식을 GPT 경량 모델로 반영"]
    B["장면 분할<br/>실패 시 시간 간격 기반 분할"]
    C["Claude 독립 검증"]
    D["GPT 독립 검증"]
    E["지적 결과 비교 · 교정안 동등성 확인"]
    F["필터링 · 반대 모델의 개선 여부 판정"]
    G["통과한 교정안의 한국어 역번역<br/>모든 번역 교정은 pending"]
    H["성별·용어집 표기 보호 · 최종 형식 점검"]
    I["결과 저장 · 새 용어집 항목 추출 · review 상태"]
    A --> B
    B --> C & D
    C & D --> E --> F --> G --> H --> I
```

- **같은 입력으로 독립 검증**: Claude의 `correct_primary`와 GPT의 `verify_and_refine`은 같은 전처리 결과를 병렬로 검토합니다. 한국어와 번역이 모두 있는 줄을 검증하며, 번역만 있는 줄은 경고를 남기고 건너뜁니다.
- **합의는 자동 승인이 아님**: 같은 줄을 지적한 경우 교정안이 동등한지 두 모델이 확인합니다. 합의한 교정안은 `claude+gpt`로 표시하지만, 다른 번역 교정안과 마찬가지로 `pending` 상태로 저장합니다.
- **교정안 필터링**: 합의하지 못한 자연스러움·뉘앙스 지적(`unnatural_style`, `nuance_tone`)은 걸러냅니다. 남은 교정안도 반대 모델의 개선 여부 판정을 통과해야 합니다.
- **판정과 역번역 분리**: 개선 여부를 먼저 판단하고, 통과한 교정안은 반대 모델이 한국어로 역번역합니다. 역번역 요청에서는 참조 한국어 대사를 제거해 결과가 원문에 끌려가지 않도록 하며, 5개 단위로 나눠 처리합니다.
- **최종 형식 점검**: 기본 제한은 한 줄 50자, 최대 2줄입니다. 줄바꿈 조정을 먼저 시도하고 필요하면 모델로 축약합니다. 대기 중인 번역 교정이 있는 줄은 교정안 쪽을 점검해 별도 자동보정과 충돌하지 않도록 합니다.
- **용어집 보호와 추출**: 교정안이 기존 표기를 훼손하지 않도록 보호하고, 정렬된 대사에서 새 용어·언어별 표기를 추출해 저장합니다. 자동 추출은 이미 저장된 표기를 덮어쓰지 않습니다.

## 검수와 내보내기

### 검수 화면

왼쪽에는 영상과 전체 자막, 오른쪽에는 교정 카드가 표시됩니다. 카테고리·상태 필터, 검수 진행률, 작품별로 기억하는 검수자 이름을 제공합니다. 카드를 선택하면 해당 구간을 재생하며, 전체 자막도 재생 위치에 맞춰 이동합니다.

| 기능 | 동작 |
|---|---|
| 승인·반려·수정 | 교정안을 채택하거나 원문을 유지하고, 직접 입력한 문장으로 확정합니다. 직접 수정한 문장이 형식 제한을 넘으면 저장을 거절합니다. |
| Claude·GPT 비교 | 같은 줄의 두 교정안을 나란히 보여줍니다. 하나를 선택하면 다른 안은 함께 반려하며, 둘 다 반려하면 원문을 유지합니다. |
| 재질의 | 검수자 지시를 반영해 교정안과 역번역을 갱신하고 다시 승인 대기 상태로 만듭니다. |
| 한국어 대사 교정 | STT 교정 이력을 저장하고 GPT로 재검증합니다. 새로 생긴 성별 모호성도 확인합니다. |
| 번역 직접 편집 | 반려되지 않은 finding이 없는 줄은 전체 자막에서 직접 편집할 수 있습니다. 이 편집에는 별도 변경 이력이 없습니다. |
| 미매칭 줄 처리 | 번역만 남은 줄을 최종 결과에서 제외하거나 다시 포함할 수 있습니다. |
| 용어집 | 아카이브와 검수 패널에서 같은 용어집을 사용하며, 항목 추가·삭제와 언어별 표기 수정을 지원합니다. |

### 내보내기

1. `GET /target-versions/{id}/export`가 승인·수정 결과를 반영한 SRT, 통계, 경고, 파일명을 만듭니다. 같은 줄에 여러 결과가 있으면 검수자 판단을 우선하며, 승인되지 않은 번역 제안은 적용하지 않습니다. 제외 표시되었거나 번역 텍스트가 없는 줄은 빼고 시간순으로 번호를 다시 매깁니다.
2. 최종 줄 길이와 용어집 일관성을 점검합니다. 용어집 불일치 후보는 GPT 경량 모델이 활용형·대명사·생략 등 문맥을 확인해 거르며, 경고에는 실제 대체 표기와 한국어 뜻 등 판단 근거를 제공합니다.
3. 경고가 없으면 바로 다운로드하고, 있으면 경고 창에서 확인합니다. **경고나 미검수 항목이 남아 있어도 다운로드할 수 있습니다.**
4. 경고 창을 닫았다 다시 열 때는 기존 결과를 재사용합니다. 검수 내용을 바꾼 뒤 `반영하기`를 누르면 `/export/reassemble`로 SRT만 다시 조립합니다. 이때 모델 점검을 반복하지 않으므로 경고 목록은 최초 점검 시점의 결과입니다.
5. 다운로드 시 `/export/confirm`으로 내보내기 이력을 기록합니다. 다운로드 파일은 SRT이며, 통계는 API 응답과 이력에 사용합니다. 검수용 영상 프록시는 유지됩니다.

## 데이터와 파일 보관

```mermaid
flowchart LR
    T["Title · 작품"] --> E["Episode · 회차"]
    E --> V["TargetVersion · 언어/지역별 자막"]
    V --> S["Segment · 정렬된 대사"]
    V --> F["FindingRow · 교정과 검수 상태"]
    V --> X["ExportRow · 내보내기 이력"]
    T --> G["CharacterGenderFact · 인물 성별"]
    T --> L["GlossaryEntry · 용어와 별칭"]
    L --> P["GlossarySpelling · 언어/지역별 표기"]
```

- 인물 성별과 용어집은 작품 단위로 공유해 다른 회차·언어판에서도 재사용합니다. 용어집은 인물·장소·업체·직함 등을 분류하고 한국어 용어·별칭·언어별 표기를 저장합니다.
- 영상 원본, 번역 SRT, 한국어 SRT, 영상 프록시는 각각 `backend/media/video/`, `srt/`, `srt_ko/`, `video_proxy/`에 저장합니다. 원본 영상은 Phase 1 저장 성공 후 삭제하며, 이후 검수는 프록시를 사용합니다.
- **재분석은 해당 언어판의 기존 세그먼트·교정·STT 교정 기록을 초기화합니다.** 분석 결과의 모든 버전을 누적 보관하는 구조는 아닙니다.
- 작품·언어판 삭제는 DB에서 논리 삭제로 처리합니다. 작품을 삭제하면 관련 원본 영상과 프록시도 삭제합니다.

## 프로젝트 구조

| 경로 | 역할 |
|---|---|
| `backend/app/main.py` | FastAPI 앱, 라우터 등록, 서버 재시작 시 중단된 분석 상태 정리 |
| `backend/app/background.py` | Phase 1·2 백그라운드 실행, 캐시·프록시 관리, 결과 저장 |
| `backend/app/models.py`, `db.py`, `schemas.py` | ORM 모델, 비동기 DB 연결, 공용 데이터 모델 |
| `backend/app/repositories.py` | 분석 결과 저장·복원, 작품별 인물 성별 재사용 |
| `backend/app/routers/titles.py` | 작품·회차, 인물 성별, 용어집, 저장 공간 관리 |
| `backend/app/routers/analysis.py` | 언어판 생성, 분석·재분석, 성별·격식 확인 완료 |
| `backend/app/routers/findings.py` | 교정·세그먼트 조회, 검수 액션, 재질의, STT 교정 |
| `backend/app/routers/export.py` | 내보내기 점검, 재조립, 다운로드 이력 |
| `backend/app/routers/uploads.py` | 영상·번역 SRT·한국어 SRT 업로드 |
| `backend/app/core/pipeline.py` | Phase 1·2 분석 흐름과 모델 결과 통합 |
| `backend/app/core/ingest.py` | SRT 파싱·조립, 오디오 추출·분할, 영상 프록시 생성 |
| `backend/app/core/alignment.py`, `embedding_dp_alignment.py`, `stt_srt_matching.py`, `time_overlap.py` | STT·한국어 SRT·번역 자막의 정렬과 타이밍 계산 |
| `backend/app/core/pretreatment.py` | CTA 제거와 비속어 사전 전처리 |
| `backend/app/core/grammar_necessity.py` | spaCy·kiwipiepy 기반 성별·격식 후보 탐지와 판단 |
| `backend/app/core/format_rules.py`, `safety_net.py` | 온점·줄 길이 규칙과 최종 형식 보정 |
| `backend/app/core/glossary_guard.py` | 교정·승인 시 용어집 표기 보호 |
| `backend/app/core/requery.py`, `export.py` | 교정 재질의, 최종 SRT 조립·통계 |
| `backend/app/core/uploads.py`, `validation.py` | 업로드 저장, 한국어 SRT 등록 경로 검증 |
| `backend/app/providers/base.py` | 모델 인터페이스, 공통 검증 프롬프트, 프로바이더 생성 |
| `backend/app/providers/live.py`, `claude_client.py`, `gpt_client.py`, `mock.py` | 실제 모델 연동과 테스트용 구현 |
| `backend/app/language_profiles/`, `knowledge/` | 언어별 YAML 설정, CTA·비속어·민감어·호칭·관용구 자료. 작품별 용어집은 DB에 저장 |
| `backend/alembic/`, `backend/tests/` | DB 마이그레이션, 백엔드 회귀 테스트 |
| `frontend/src/App.jsx` | `titles`·`confirm`·`review` 화면 전환과 화면 상태 복원 |
| `frontend/src/views/TitleListView.jsx` | 아카이브 화면의 헤더와 레이아웃 |
| `frontend/src/views/TitleArchiveList.jsx` | 작품 목록·상세·신규 등록, 회차·언어판·성별·용어집 관리 |
| `frontend/src/views/RegisterConfirmationView.jsx`, `FlaggedSegmentStepper.jsx` | 미확정 성별·격식 확인과 구간별 영상 재생 |
| `frontend/src/views/ReviewView.jsx` | 영상·전체 자막·교정 카드, 검수·내보내기 |
| `frontend/src/components/GlossaryTable.jsx`, `Disclosure.jsx`, `FileDropzone.jsx`, `QQLogo.jsx` | 공용 용어집·접기 UI·업로드·로고 |
| `frontend/src/api.js` | REST 클라이언트와 업로드 진행률 |
| `deploy/` | 서버 준비 스크립트와 배포 런북 |

`docs/`의 목업·설계 문서는 설계 참고 자료이며, 현재 동작과 차이가 있을 수 있습니다.

## 실행 방법

### 준비

로컬 실행에는 Python 환경, PostgreSQL, ffmpeg, Node.js·npm이 필요합니다. 배포 이미지의 기준은 Python 3.14, Node.js 22, PostgreSQL 16입니다.

처음 설치할 때 프로젝트 루트에서 실행합니다.

```bash
python3 -m venv backend/venv
backend/venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm install)
```

`backend/.env.example`을 참고해 `backend/.env`를 준비합니다. 기존 `.env`가 있다면 필요한 값만 확인합니다.

| 환경변수 | 용도 |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://사용자:암호@호스트/DB이름` 형식의 연결 주소 |
| `QC_PROVIDER` | 운영은 `live`. `mock`은 pytest 환경에서만 허용 |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | Claude API 키와 검증 모델, 필수 |
| `OPENAI_API_KEY`, `GPT_MODEL` | OpenAI API 키와 검증 모델, 필수 |
| `CLAUDE_LIGHT_MODEL`, `GPT_LIGHT_MODEL` | 보조 작업용 모델 |
| `GPT_TRANSCRIBE_MODEL` | STT 모델 |

DB는 먼저 생성해야 합니다. `DATABASE_URL`을 생략하면 로컬 `sub_translation_qc_es` DB를 사용합니다. spaCy 언어별 모델과 kiwipiepy는 `requirements.txt`에 포함되어 있습니다.

### 백엔드

```bash
cd backend
venv/bin/alembic upgrade head
venv/bin/uvicorn app.main:app --reload
```

### 프론트엔드

별도 터미널에서 실행합니다.

```bash
cd frontend
npm run dev
```

Vite가 `/api`, `/media` 요청을 `localhost:8000`의 백엔드로 프록시합니다.

### 테스트와 빌드

백엔드 테스트에는 실행 중인 로컬 PostgreSQL과 **별도 테스트 DB `sub_translation_qc_es_test`**가 필요합니다. `backend/conftest.py`가 앱을 불러오기 전에 연결 주소를 다음 값으로 강제 설정합니다.

```text
postgresql+asyncpg://postgres:postgres@localhost/sub_translation_qc_es_test
```

테스트 픽스처는 이 테스트 DB의 테이블을 생성·삭제합니다. 개발 DB와 분리되어 있지만, 테스트 DB에 보관할 데이터를 넣으면 안 됩니다.

프로젝트 루트에서 실행합니다.

```bash
(cd backend && venv/bin/pytest -q)
(cd frontend && npm run build && npm run lint)
```

백엔드는 pytest 기반이며, 프론트엔드는 Vite 빌드와 Oxlint를 사용합니다. 프론트엔드 전용 테스트 러너는 현재 설정되어 있지 않습니다.

## 배포

Docker Compose로 `backend`, `frontend`(Caddy), `postgres`를 함께 실행합니다. 배포 환경변수와 도메인·인증 설정은 [배포 런북](deploy/README.md)을 따릅니다.

```bash
docker compose up -d --build
```

- 백엔드는 시작 시 Alembic 마이그레이션 후 Uvicorn을 실행하며, 호스트의 `127.0.0.1:8000`에 바인딩합니다.
- Caddy가 정적 프론트엔드, HTTPS, Basic Auth, API·미디어 프록시, SPA fallback을 제공합니다.
- PostgreSQL은 Docker 내부망으로 연결하며 호스트 포트를 노출하지 않습니다. DB·미디어·Caddy 데이터는 볼륨에 보관합니다.
- `deploy/ec2-bootstrap.sh`는 서버의 Docker 설치와 스왑 준비를 지원합니다.

## 현재 제약

- **서버 프로세스에 종속된 분석**: 별도 작업 큐가 없습니다. 서버 재시작 시 진행 중이던 `analyzing`·`verifying` 작업은 실패 상태로 정리되며 자동 재개되지 않습니다. 확인 대기와 검수 상태는 유지합니다.
- **동시 작업 제어 없음**: 같은 언어판의 분석을 중복 호출하면 결과 초기화·저장이 충돌하고 API 비용이 중복될 수 있습니다. 같은 교정을 여러 사람이 수정하면 나중 요청이 덮어씁니다. 검수자 이름은 표시용이며 개별 로그인 계정이 아닙니다.
- **재분석 입력 보존 조건**: 원본 영상 삭제 후에는 한국어 SRT·프록시 또는 사용 가능한 STT 캐시·프록시에 의존합니다. 필요한 자료가 없으면 재분석이 실패할 수 있으며, 기존 회차의 원본 영상을 교체하는 전용 UI는 없습니다.
- **완료 상태가 전 항목 점검 성공을 보장하지 않음**: 일부 보조 단계 실패나 미매칭 줄은 경고를 남기고 검수 단계로 넘어갑니다. 분석 경고와 미검수 교정안을 함께 확인해야 합니다.
- **내보내기는 최종 승인 관문이 아님**: 미검수 항목·경고가 있어도 다운로드할 수 있고, 재조립은 기존 경고를 새로 검사하지 않습니다.
- **형태소 모델의 메모리 사용**: 특히 프랑스어 `fr_core_news_lg`는 크기가 커 저사양 서버의 메모리가 부족할 수 있습니다. 배포 스크립트의 스왑 설정을 참고합니다.
