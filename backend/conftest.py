import sys
import os

# Add the backend directory to the Python path so that app module can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 자동 테스트는 실제 개발용 데이터베이스(sub_translation_qc_es)를 절대 건드리면
# 안 된다 — 테스트 픽스처가 매 테스트마다 스키마를 create/drop하므로, 개발용 DB와
# 공유하면 테스트를 돌릴 때마다 개발용 데이터가 통째로 사라진다. 이 파일은
# pytest가 가장 먼저 읽는 conftest.py라 app.db가 import되기 전에(app.db는
# import 시점에 DATABASE_URL을 한 번만 읽어 엔진을 만든다) 여기서 값을
# 덮어써야 확실하게 적용된다.
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost/sub_translation_qc_es_test"
