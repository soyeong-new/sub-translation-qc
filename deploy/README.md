# 배포 런북 (AWS EC2 프리 티어)

설계 배경은 `docs/superpowers/specs/2026-08-28-aws-deployment-design.md` 참고.

## 1. 도메인 준비

아무 등록업체에서나 도메인을 구매한다. DNS A 레코드는 이후 3번에서
받는 Elastic IP를 알기 전까지는 비워둔다.

## 2. EC2 인스턴스 생성 (AWS 콘솔)

- AMI: Ubuntu 22.04 LTS
- 인스턴스 타입: t3.micro (프리 티어)
- 키 페어: 새로 생성해 다운로드 (SSH 접속용)
- 보안 그룹:
  - 인바운드 80(HTTP): 0.0.0.0/0
  - 인바운드 443(HTTPS): 0.0.0.0/0
  - 인바운드 22(SSH): 내 IP만
  - 그 외 인바운드 전부 없음 (5432 등 아무것도 열지 않는다)

## 3. Elastic IP 할당

EC2 콘솔 → Elastic IP → 할당 → 방금 만든 인스턴스에 연결. 이 IP를
1번 도메인의 DNS A 레코드로 등록한다 (전파에 최대 몇 시간).

## 4. 서버 최초 설정

```bash
ssh -i <키페어.pem> ubuntu@<Elastic IP>

git clone <이 저장소 URL> sub_translation_qc
cd sub_translation_qc

chmod +x deploy/ec2-bootstrap.sh
./deploy/ec2-bootstrap.sh
# "재로그인 필요" 메시지가 뜨면 exit 후 다시 ssh 접속
```

## 5. 시크릿 배치

로컬에서 서버로 `backend/.env`를 복사한다 (`backend/.env.example`이
어떤 값이 필요한지 보여준다 — API 키는 실제 운영 키를 채운다):

```bash
scp -i <키페어.pem> backend/.env ubuntu@<Elastic IP>:~/sub_translation_qc/backend/.env
```

서버에서 루트 `.env`를 만든다:

```bash
cd sub_translation_qc
cp .env.example .env
```

`.env`를 열어(`nano .env`):
- `DOMAIN`을 1번에서 산 실제 도메인으로 바꾼다
- `BASIC_AUTH_USER`를 원하는 아이디로 바꾼다
- `BASIC_AUTH_HASH`를 채운다:
  ```bash
  docker run --rm caddy:2-alpine caddy hash-password --plaintext <원하는 비밀번호>
  ```
  결과 값의 모든 `$`를 `$$`로 바꿔서 붙여넣는다 (예:
  `$2a$14$Pl...` → `$$2a$$14$$Pl...`) — 안 그러면 docker compose가
  `.env` 파일의 `$`를 변수 치환으로 오인해 해시를 깨뜨린다.

## 6. 최초 배포

```bash
docker compose up -d --build
docker compose logs -f frontend   # Caddy가 인증서 발급하는 로그 확인, 에러 없이 "certificate obtained successfully" 나오면 성공
```

브라우저로 `https://<도메인>` 접속 → Basic Auth 로그인 창이 뜨면 성공.

## 7. 이후 업데이트 배포

로컬에서:
```bash
git push origin main
```

서버에서:
```bash
cd sub_translation_qc
git pull
docker compose up -d --build
```

## 알려진 제약 (지금은 손 안 댐)

- Postgres 백업 없음 — 필요해지면 cron + `pg_dump` 추가
- 인증서 갱신 실패 알림 없음 — `docker compose logs frontend`로만 확인 가능
- 미디어(영상) 저장 공간이 EBS 기본 30GB를 넘으면 AWS 콘솔에서 볼륨
  확장 필요 — 지금은 모니터링만 (`df -h`)
