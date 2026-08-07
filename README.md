# Sub Translation QC (ES)

한국어 원본 영상과 스페인어(LATAM) 번역 자막(SRT)을 입력받아, STT·정렬·포맷·번역품질·민감어를 자동으로 점검하고 사람이 검수해 최종 SRT를 내보내는 자막 QC 도구입니다.

- **백엔드**: FastAPI + SQLAlchemy(async) + PostgreSQL + Alembic
- **프론트엔드**: React 19 + Vite + Tailwind
- **모델 연동**: `ModelProvider` 추상 인터페이스. Claude(Anthropic)·GPT(OpenAI)를 쓰는 `LiveModelProvider`가 실제 운영 경로이고, 테스트 전용 `MockProvider`가 있습니다. 성별/격식(존댓말·반말) 판단이 필요한 줄을 거르는 문법 필요성 체크는 LLM이 아니라 spaCy 형태소 분석(파이썬)으로 처리합니다.

## 목차

- [전체 흐름 한눈에 보기](#전체-흐름-한눈에-보기)
- [전체 파이프라인](#전체-파이프라인)
- [프로젝트 구조](#프로젝트-구조)
- [아키텍처 레이어](#아키텍처-레이어)
- [실행 방법](#실행-방법)
- [알려진 제약](#알려진-제약)

## 전체 흐름 한눈에 보기

이 도구가 실제로 무슨 일을 하는지, 개발 지식 없이도 이해할 수 있도록 정리한 그림입니다.

```mermaid
flowchart TD
    subgraph P1["① 자동 분석"]
        direction TD
        S1(["1. 작품 등록\n영상 파일과 스페인어 자막 파일을 올린다"])
        S2["2. 자동으로 대사를 받아 적는다\n(영상 속 한국어 음성을 텍스트로 변환)"]
        S3["3. 한국어 대사와 스페인어 자막을 줄 단위로 짝짓는다\n어느 대사에 어느 번역이 대응하는지 맞춘다"]
        S4["4. 자막 형식을 점검한다\n한 줄이 너무 길지 않은지, 말줄임표 표기가 맞는지 등"]
        S5["5. 성별·존댓말 판단이 필요한 줄만 골라낸다\n(문법적으로만 판단, 누가 말했는지는 모름)"]
        S6["6. 골라낸 줄은 검수자에게 확인을 요청할\n목록으로 남긴다"]
        S7["7. 번역이 정확하고 자연스러운지\nClaude와 GPT 두 AI가 서로 뭘 하는지 모른 채\n각각 독립적으로 점검한다"]
        S7b["7-1. 둘 다 같은 문제를 지적하면(합의) 자동 반영,\n한쪽만 지적하면(불일치) 사람 확인 전까지 원문 유지"]
        S8["8. 사전에 없는 애매한 비속어가 있는지\n같은 두 AI 검증에서 함께 점검한다"]
        S9["9. 발견된 문제들을 한 목록으로 모은다"]

        S1 --> S2 --> S3 --> S4 --> S9
        S3 --> S5 --> S6 --> S9
        S3 --> S7 --> S7b --> S9
        S3 --> S8 --> S9
    end

    STORE[("② 보관소\n대사, 문제 목록을\n전부 기록해 계속 쌓아둔다\n(나중에 언제든 다시 불러올 수 있음)")]

    subgraph P2["③ 사람이 검수"]
        direction TD
        S10(["10. 검수자가 보관소에 쌓인 내용을\n화면에서 하나씩 불러와 확인한다\n그대로 승인 / 직접 수정 / 반려"])
        S11["11. 성별·존댓말이 필요한 줄은\n영상을 보면서 검수자가 직접 확정한다"]
    end

    subgraph P3["④ 최종 결과물 만들기"]
        direction TD
        S12["12. 검수자의 최종 판단을 우선 반영해\n완성된 자막 파일을 만든다"]
        S13(["13. 완성된 자막 파일과\n전체 반영 비율(통계)을 내려받는다"])
    end

    S9 --> STORE
    STORE -->|"불러오기"| S10
    STORE -->|"불러오기"| S11
    S10 -->|"확인 결과를\n다시 기록"| STORE
    S11 -->|"확정 결과를\n다시 기록"| STORE
    STORE -->|"검수 완료된 내용 불러오기"| S12
    S12 --> S13
    S13 -->|"내보낸 이력을\n다시 기록"| STORE
```

**요약하면:**
1. **① 자동 분석** — 영상과 자막을 올리면 시스템이 대사를 받아 적고, 번역을 서로 맞춰본 뒤 형식·번역품질·민감어 문제를 자동으로 찾아 목록으로 정리합니다. 번역 품질은 Claude와 GPT가 같은 원문을 각자 독립적으로 검토해, 둘이 동의한 것만 자동 반영하고 의견이 갈리면 사람 확인 전까지 원문을 그대로 둡니다.
2. **② 보관소** — 이 목록은 전부 한곳에 기록해 쌓아두기 때문에, 나중에 언제 다시 열어도 이어서 검수할 수 있습니다.
3. **③ 사람이 검수** — 검수자는 보관소에 쌓인 내용을 불러와 승인/수정/반려하고, 성별·존댓말처럼 영상을 봐야 아는 판단은 직접 확정합니다. 그 판단은 다시 보관소에 기록됩니다.
4. **④ 최종 결과물 만들기** — 검수가 끝난 내용을 불러와 검수자의 판단을 우선 반영한 최종 자막 파일을 만들고, 언제·얼마나 반영해서 내보냈는지 이력도 보관소에 남깁니다.

즉 "자동 분석해서 쌓아두기"와 "그걸 사람이 검수해서 최종 파일로 뽑아내기"는 서로 다른 단계이며, 그 사이를 **보관소**가 이어줍니다.

## 전체 파이프라인

작품 등록부터 최종 SRT 내보내기까지 전체 흐름입니다(개발자용). `run-analysis`가 호출되면 `backend/app/core/pipeline.py`의 `run_pipeline()`이 아래 단계를 순서대로 오케스트레이션합니다.

```mermaid
flowchart TD
    subgraph UI["프론트엔드"]
        A1["작품 등록 화면\nTitleListView.jsx"] -->|"영상 드래그/선택"| A2["FileDropzone.jsx"]
        A1 -->|"SRT 드래그/선택"| A2
        A2 -->|"XHR 업로드 진행률"| A3["api.js\nuploadVideo / uploadSrt"]
    end

    subgraph UP["업로드"]
        A3 -->|"POST /uploads/video"| B1["uploads.py\nsave_upload()"]
        A3 -->|"POST /uploads/srt"| B1
        B1 -->|"경로 검증 + UUID 파일명"| B2[("backend/media/\nvideo/ · srt/")]
    end

    B2 --> C0["POST /titles → /episodes → /target-versions\n(title/episode/target_version 생성)"]
    C0 --> C1["POST /run-analysis\ntarget_srt_path 전달"]

    subgraph PIPE["파이프라인 (pipeline.run_pipeline)"]
        C1 --> D1["ingest.load_srt()\nSRT 파싱"]
        C1 --> D2["ingest.extract_audio()\n영상 → wav"]
        D2 --> D2b["ingest.split_audio_into_chunks()\nSTT API 길이 제한에 맞춰 분할"]
        D2b --> D3["provider.transcribe()\n조각별 병렬 STT → 오프셋 병합"]
        D1 --> D4
        D3 --> D4["alignment.align()\n한국어·타겟 세그먼트 정렬"]
        D4 --> D5["format_rules.check_ellipsis()\n온점 자동보정(1차)"]
        D5 --> D6["pretreatment.run_pretreatment()\n등록된 글로서리/CTA 문구/비속어\n사전 자동 치환 (LLM 없음)"]
        D6 --> D7["grammar_necessity.check_grammar_necessity()\nspaCy 형태소 분석(파이썬, LLM 아님)\n성별/격식 판단이 필요한 줄만 골라 플래그"]

        D6 --> D8A["provider.correct_primary()\nClaude 독립 검증"]
        D6 --> D8B["provider.verify_and_refine()\nGPT 독립 검증"]
        D8A -.->|"asyncio.gather — 같은 원문을 동시에,\n서로 뭘 하는지 모른 채 검증(앵커링 편향 방지)"| D8B
        D8A --> D9["_reconcile_dual_verification()\nsegment_id 기준 합의/불일치 비교"]
        D8B --> D9
        D9 -->|"합의: 둘 다 지적"| D10A["자동 적용\nFinding.status = approved"]
        D9 -->|"불일치: 한쪽만 지적"| D10B["원문 유지\nFinding.status = pending"]
        D10A --> D11["back_translate_with_claude/gpt\n반대쪽 모델이 한국어로 역번역\n(감사·참고용, description에 첨부)"]
        D10B --> D11

        D11 --> D12["safety_net.shrink_violating_lines()\n모든 교정이 끝난 뒤 글자수 위반 최종 재검사"]
        D7 --> D13
        D12 --> D13["findings 취합"]
    end

    D13 --> E1["repositories.save_pipeline_result()"]
    E1 --> E2[("PostgreSQL")]

    subgraph REVIEW["검수 화면"]
        E2 --> F1["GET /findings, /flagged-segments"]
        F1 --> F2["ReviewView.jsx\nfindings 승인 · 거절 · 수정"]
        F2 -->|"POST /findings/{id}/review-action"| E2
        F1 --> F3["FlaggedSegmentStepper.jsx\n영상 보며 성별/격식 직접 확정"]
        F3 -->|"POST /segments/{id}/resolve-gender\nPOST /segments/{id}/resolve-formality"| E2
    end

    E2 --> G1["GET /export"]
    G1 --> G2["export.assemble_final_srt()\n검수자 판단 우선 반영"]
    G1 --> G3["export.compute_stats()\n반영율 통계"]
    G2 --> G4["최종 SRT + 통계 응답"]
    G3 --> G4
```

**핵심 설계 포인트**

- 오디오는 STT 단계에서 **딱 한 번만** 사용되고, 이후 모든 검사는 텍스트(정렬된 pair) 기준으로 동작합니다.
- 온점(ellipsis) 자동보정은 원본 텍스트로 먼저 위반을 감지·기록한 뒤 적용해, 무엇이 왜 바뀌었는지 추적할 수 있습니다.
- **Claude/GPT는 순차적으로 서로의 결과를 이어받지 않고, 같은 원문을 동시에 독립적으로 검증합니다.** 검수자가 스페인어를 몰라도 운영 가능해야 하므로, 두 모델의 일치 여부가 신뢰도 신호가 됩니다 — 합의된 교정만 자동 적용되고, 의견이 갈리면 원문을 유지한 채 반대쪽 모델의 한국어 역번역을 참고용으로 붙여 사람이 판단할 근거를 남깁니다.
- 성별/격식(존댓말·반말)은 화자를 텍스트만으로 특정할 근거가 없어 Claude/GPT 어느 쪽도 판단하지 않습니다. spaCy로 "판단이 필요한 줄인지"만 걸러내고, 실제 값은 검수자가 영상을 보고 직접 확정합니다.
- Export 시 같은 세그먼트에 자동보정과 검수자 판단이 동시에 걸리면 **검수자 판단이 항상 우선**합니다 (`reviewed_at` 유무로 정렬).

## 프로젝트 구조

```mermaid
flowchart LR
    subgraph FE["frontend/src"]
        direction TB
        FE1["App.jsx\n화면 전환 라우팅"]
        FE2["views/TitleListView.jsx\n작품 등록 + 파일 업로드"]
        FE3["views/ReviewView.jsx\nfindings 검수 화면"]
        FE6["views/FlaggedSegmentStepper.jsx\n성별/격식 확인 스테퍼"]
        FE4["components/FileDropzone.jsx\n드래그앤드롭 업로드 UI"]
        FE5["api.js\nREST 클라이언트"]
        FE1 --> FE2
        FE1 --> FE3
        FE3 --> FE6
        FE2 --> FE4
        FE2 --> FE5
        FE3 --> FE5
        FE6 --> FE5
    end

    subgraph BE["backend/app"]
        direction TB
        BE1["main.py\nFastAPI 앱 + 라우터 등록"]
        BE2["repositories.py\nDB 영속화 (ID 네임스페이싱)"]
        BE3["models.py\nSQLAlchemy ORM"]
        BE4["db.py\n비동기 엔진/세션"]
        BE5["schemas.py\n공용 Pydantic 모델"]

        subgraph ROUT["routers/"]
            direction TB
            R1["titles.py"]
            R2["analysis.py"]
            R3["findings.py"]
            R4["export.py"]
            R5["uploads.py"]
        end

        subgraph CORE["core/"]
            direction TB
            CP["pipeline.py\n(오케스트레이터)"]
            C1["ingest.py"]
            C2["alignment.py"]
            C3["format_rules.py"]
            C10["pretreatment.py\n글로서리/CTA/비속어 사전 자동 치환"]
            C11["grammar_necessity.py\nspaCy 성별/격식 필요성 판단"]
            C12["safety_net.py\n글자수 위반 최종 재교정"]
            C13["pronoun_hints.py\n영어 SRT 대명사 힌트"]
            C14["requery.py\nfinding 재질의"]
            C8["export.py"]
            C9["uploads.py"]
        end

        subgraph CFG["설정/지식"]
            direction TB
            K1["language_profiles/\nes_LATAM.yaml + loader.py"]
            K2["knowledge/\nglossary · profanity_dictionary ·\nsensitive_terms.yaml + loader.py"]
        end

        subgraph PROV["providers/"]
            direction TB
            P1["base.py\nModelProvider 인터페이스"]
            P3["claude_client.py"]
            P4["gpt_client.py"]
            P5["live.py\n(Claude+GPT 조합)"]
            P2["mock.py\n테스트용 구현체"]
        end

        BE1 --> ROUT
        R2 --> CP
        R5 --> C9
        BE1 --> BE2 --> BE3 --> BE4
        CP --> C1 & C2 & C3 & C10 & C11 & C12 & C13
        CP --> K1 & K2
        R2 --> P1
        P1 --> P5 --> P3 & P4
        P1 --> P2
        R3 --> C14
        R4 --> C8
    end

    FE5 -->|"HTTP /api"| BE1
```

| 경로 | 역할 |
|---|---|
| `backend/app/main.py` | FastAPI 앱 생성 + 라우터 등록 |
| `backend/app/routers/titles.py` | 작품/에피소드 등록, 언어 프로필 목록 |
| `backend/app/routers/analysis.py` | target-version 생성, run-analysis(파이프라인 실행) |
| `backend/app/routers/findings.py` | findings/segments 조회, 검수 액션, 성별/격식 확정, requery, STT 교정 |
| `backend/app/routers/export.py` | 최종 SRT 조립 + 반영율 통계 |
| `backend/app/routers/uploads.py` | 영상/SRT 업로드 |
| `backend/app/core/pipeline.py` | 분석 파이프라인 오케스트레이터 |
| `backend/app/core/ingest.py` | SRT 파싱/조립, 영상→오디오 추출, 오디오 조각 분할 |
| `backend/app/core/alignment.py` | 한국어 STT ↔ 대상언어 자막 타임코드 정렬 |
| `backend/app/core/format_rules.py` | 줄 길이/연속 온점 등 언어 무관 포맷 규칙 |
| `backend/app/core/pretreatment.py` | 등록된 글로서리·CTA 문구·비속어 사전 자동 치환 (LLM 없음) |
| `backend/app/core/grammar_necessity.py` | spaCy 형태소 분석으로 성별/격식 판단이 필요한 줄만 판별 (LLM 아님) |
| `backend/app/core/safety_net.py` | 모든 교정 후 글자수 위반 최종 재교정 |
| `backend/app/core/pronoun_hints.py` | 영어 SRT 대조로 성별 확인용 대명사 힌트 계산 |
| `backend/app/core/requery.py` | 검수자 지시사항 반영한 finding 재질의 |
| `backend/app/core/export.py` | 최종 SRT 조립 + 반영율 통계 |
| `backend/app/core/uploads.py` | 업로드 파일을 경로 조작 없이 디스크에 저장 |
| `backend/app/repositories.py` | 파이프라인 결과 DB 영속화 (target_version별 ID 네임스페이싱) |
| `backend/app/models.py` / `db.py` / `schemas.py` | ORM 테이블 / DB 엔진·세션 / 공용 데이터 모델 |
| `backend/app/language_profiles/` | 언어별 설정 (YAML, 현재 `es_LATAM`) |
| `backend/app/knowledge/` | 글로서리·비속어 사전·민감어 목록 (YAML) |
| `backend/app/providers/base.py` | `ModelProvider` 추상 인터페이스 |
| `backend/app/providers/claude_client.py` / `gpt_client.py` | Claude/GPT API 얇은 SDK 래퍼 (검증 + 역번역) |
| `backend/app/providers/live.py` | Claude+GPT를 묶는 실제 운영 프로바이더 |
| `backend/app/providers/mock.py` | 테스트용 결정론적 구현체 |
| `frontend/src/views/TitleListView.jsx` | 작품 등록 + 영상/SRT 드래그앤드롭 업로드 화면 |
| `frontend/src/views/ReviewView.jsx` | findings 검수, STT 교정, export 화면 |
| `frontend/src/views/FlaggedSegmentStepper.jsx` | 영상 보며 성별/격식 한 줄씩 확인하는 풀스크린 스테퍼 |
| `frontend/src/components/FileDropzone.jsx` | 재사용 가능한 드래그앤드롭/클릭 업로드 컴포넌트 |
| `frontend/src/api.js` | 백엔드 REST API 클라이언트 (업로드는 XHR로 진행률 지원) |

## 아키텍처 레이어

```mermaid
flowchart TB
    Browser["브라우저\n(React SPA)"] -->|"fetch / XHR"| API["FastAPI\nrouters/*.py"]
    API --> Pipeline["파이프라인\ncore/*.py"]
    API --> Repo["repositories.py"]
    Pipeline --> Provider["ModelProvider\nSTT(GPT) · 검증(Claude+GPT 병렬) · 역번역"]
    Pipeline --> Grammar["grammar_necessity.py\nspaCy(파이썬, LLM 아님)"]
    Pipeline --> Config["language_profiles/\nknowledge/ (YAML)"]
    Repo --> DB[("PostgreSQL")]
    API --> Media[("backend/media/\nvideo · srt")]
```

## 실행 방법

### 백엔드

```bash
cd backend
venv/bin/uvicorn app.main:app --reload
```

DB 마이그레이션이 필요한 경우:

```bash
cd backend
venv/bin/alembic upgrade head
```

### 프론트엔드

```bash
cd frontend
npm run dev
```

### 테스트

```bash
cd backend && venv/bin/pytest -q
cd frontend && npm run build && npm run lint
```

## 알려진 제약

- **spaCy 스페인어 모델 필요**: `grammar_necessity.py`가 `es_core_news_sm`을 씁니다. `requirements.txt`에 고정 wheel URL로 포함돼 있어 `pip install -r requirements.txt`만으로 같이 설치됩니다.
- **Claude/GPT 합의 없는 교정은 자동 적용되지 않음**: 검수자가 스페인어를 몰라도 운영 가능해야 한다는 전제로, 두 모델이 같은 줄을 지적해야만 자동 반영됩니다. 한쪽만 지적한 경우는 원문이 유지된 채 역번역만 참고용으로 남고, 실제 반영 여부는 사람이 판단할 근거가 부족할 수 있습니다.
- **개발용 DB와 테스트 DB가 동일 인스턴스를 공유**: `pytest` 실행 시 테스트 픽스처가 테이블을 `drop_all`하면서 개발 중 등록한 데이터가 함께 사라질 수 있습니다. 복구는 `cd backend && venv/bin/alembic stamp base && venv/bin/alembic upgrade head`로 가능하며, 근본적으로는 테스트 DB를 분리하는 것이 권장됩니다(아직 미적용).
