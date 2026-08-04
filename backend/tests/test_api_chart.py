import asyncio
from unittest.mock import patch
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import engine, async_session
from app.models import Base, Title, Character, Relationship


@pytest.fixture(autouse=True)
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_attach_chart_image_starts_background_extraction(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setattr("app.main.MEDIA_ROOT", tmp_path)
    chart_dir = tmp_path / "chart_image"
    chart_dir.mkdir(parents=True)
    chart_file = chart_dir / "chart.png"
    chart_file.write_bytes(b"fake")

    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.commit()
        title_id = title.id

    async def _fake_extract_chart_and_save(title_id, image_path):
        async with async_session() as session:
            t = await session.get(Title, title_id)
            t.chart_extraction_status = "review_needed"
            await session.commit()

    transport = ASGITransport(app=app)
    with patch("app.main.extract_chart_and_save", side_effect=_fake_extract_chart_and_save):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(f"/titles/{title_id}/chart-image",
                                  json={"image_path": str(chart_file)})
            assert r.status_code == 200
            assert r.json()["status"] == "processing"

            if app.state.background_tasks:
                await asyncio.gather(*list(app.state.background_tasks), return_exceptions=True)

    async with async_session() as session:
        title = await session.get(Title, title_id)
        assert title.chart_image_path == str(chart_file)
        assert title.chart_extraction_status == "review_needed"


@pytest.mark.asyncio
async def test_attach_chart_image_returns_404_for_missing_title(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setattr("app.main.MEDIA_ROOT", tmp_path)
    chart_dir = tmp_path / "chart_image"
    chart_dir.mkdir(parents=True)
    chart_file = chart_dir / "chart.png"
    chart_file.write_bytes(b"fake")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/titles/does-not-exist/chart-image",
                              json={"image_path": str(chart_file)})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_attach_chart_image_rejects_path_outside_chart_image_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setattr("app.main.MEDIA_ROOT", tmp_path)
    chart_dir = tmp_path / "chart_image"
    chart_dir.mkdir(parents=True)
    # 문자열상으로는 chart_image/ 아래처럼 보이지만 ".."을 통해 실제로는
    # MEDIA_ROOT/chart_image 밖(tmp_path/etc/passwd)을 가리키는 경로.
    traversal_path = chart_dir / ".." / ".." / "etc" / "passwd"

    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/titles/{title_id}/chart-image",
                              json={"image_path": str(traversal_path)})
    assert r.status_code == 400

    async with async_session() as session:
        title = await session.get(Title, title_id)
        # 검증 실패 시 chart_image_path/status가 갱신되지 않아야 한다.
        assert title.chart_image_path is None
        assert title.chart_extraction_status == "none"


@pytest.mark.asyncio
async def test_list_titles_returns_created_titles(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        session.add(Title(name="T1", type="movie"))
        session.add(Title(name="T2", type="series"))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/titles")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()}
    assert names == {"T1", "T2"}


@pytest.mark.asyncio
async def test_get_title_returns_chart_status_and_image_url(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setattr("app.main.MEDIA_ROOT", tmp_path)
    chart_dir = tmp_path / "chart_image"
    chart_dir.mkdir(parents=True)
    chart_file = chart_dir / "chart.png"
    chart_file.write_bytes(b"fake")

    async with async_session() as session:
        title = Title(name="T", type="series", chart_image_path=str(chart_file),
                      chart_extraction_status="review_needed")
        session.add(title)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/titles/{title_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["chart_extraction_status"] == "review_needed"
    assert body["chart_image_url"] == "/media/chart_image/chart.png"


@pytest.mark.asyncio
async def test_get_title_blocks_path_traversal_in_chart_image_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setattr("app.main.MEDIA_ROOT", tmp_path)
    chart_dir = tmp_path / "chart_image"
    chart_dir.mkdir(parents=True)
    # 문자열상으로는 chart_image/ 접두사로 시작하지만 ".."을 통해 실제로는
    # chart_dir 밖(tmp_path/etc/passwd)을 가리키는 경로 — is_relative_to를
    # lexical하게만 검사하면 이 문자열이 그대로 통과해버린다.
    traversal_path = chart_dir / ".." / ".." / "etc" / "passwd"

    async with async_session() as session:
        title = Title(name="T", type="series", chart_image_path=str(traversal_path),
                      chart_extraction_status="review_needed")
        session.add(title)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/titles/{title_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["chart_image_url"] is None


@pytest.mark.asyncio
async def test_get_title_returns_404_for_missing_title(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/titles/does-not-exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_title_characters_and_relationships(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.flush()
        a = Character(title_id=title.id, label="민지", suggested_gender="female", source="chart_image")
        b = Character(title_id=title.id, label="서준")
        session.add_all([a, b])
        await session.flush()
        rel = Relationship(title_id=title.id, speaker_character_id=a.id,
                           addressee_character_id=b.id, relationship_type="연인")
        session.add(rel)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(f"/titles/{title_id}/characters")
        assert r.status_code == 200
        chars = r.json()
        assert len(chars) == 2
        minji = next(c for c in chars if c["label"] == "민지")
        assert minji["suggested_gender"] == "female"
        assert minji["source"] == "chart_image"

        r = await client.get(f"/titles/{title_id}/relationships")
        assert r.status_code == 200
        rels = r.json()
        assert len(rels) == 1
        assert rels[0]["relationship_type"] == "연인"
        assert rels[0]["speaker_label"] == "민지"
        assert rels[0]["addressee_label"] == "서준"


@pytest.mark.asyncio
async def test_create_character_then_delete(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/titles/{title_id}/characters", json={"label": "지훈"})
        assert r.status_code == 200
        char_id = r.json()["id"]
        assert r.json()["label"] == "지훈"

        r = await client.get(f"/titles/{title_id}/characters")
        assert len(r.json()) == 1

        r = await client.delete(f"/characters/{char_id}")
        assert r.status_code == 200

        r = await client.get(f"/titles/{title_id}/characters")
        assert len(r.json()) == 0


@pytest.mark.asyncio
async def test_create_character_rejects_blank_label(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/titles/{title_id}/characters", json={"label": ""})
        assert r.status_code == 400

        r = await client.post(f"/titles/{title_id}/characters", json={"label": "   "})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_character_is_idempotent_by_label(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(f"/titles/{title_id}/characters", json={"label": "지훈"})
        assert r1.status_code == 200
        r2 = await client.post(f"/titles/{title_id}/characters", json={"label": "지훈"})
        assert r2.status_code == 200
        assert r1.json()["id"] == r2.json()["id"]

        r = await client.get(f"/titles/{title_id}/characters")
        assert r.status_code == 200
        chars = r.json()
        assert len(chars) == 1
        assert chars[0]["label"] == "지훈"


@pytest.mark.asyncio
async def test_update_character_label_and_gender(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.flush()
        char = Character(title_id=title.id, label="민지")
        session.add(char)
        await session.commit()
        char_id = char.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(f"/characters/{char_id}",
                               json={"label": "김민지", "suggested_gender": "female"})
        assert r.status_code == 200
        assert r.json()["label"] == "김민지"
        assert r.json()["suggested_gender"] == "female"

        r = await client.patch(f"/characters/{char_id}", json={"label": "   "})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_character_also_removes_its_relationships(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.flush()
        a = Character(title_id=title.id, label="민지")
        b = Character(title_id=title.id, label="서준")
        session.add_all([a, b])
        await session.flush()
        rel = Relationship(title_id=title.id, speaker_character_id=a.id, addressee_character_id=b.id)
        session.add(rel)
        await session.commit()
        title_id, char_id = title.id, a.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.delete(f"/characters/{char_id}")
        assert r.status_code == 200

        r = await client.get(f"/titles/{title_id}/relationships")
        assert r.json() == []


@pytest.mark.asyncio
async def test_create_relationship_gets_or_creates_characters_by_label(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/titles/{title_id}/relationships",
                              json={"speaker_label": "민지", "addressee_label": "서준",
                                    "relationship_type": "연인"})
        assert r.status_code == 200
        assert r.json()["speaker_label"] == "민지"
        assert r.json()["relationship_type"] == "연인"

        r = await client.get(f"/titles/{title_id}/characters")
        assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_update_and_delete_relationship(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series")
        session.add(title)
        await session.flush()
        a = Character(title_id=title.id, label="민지")
        b = Character(title_id=title.id, label="서준")
        session.add_all([a, b])
        await session.flush()
        rel = Relationship(title_id=title.id, speaker_character_id=a.id, addressee_character_id=b.id)
        session.add(rel)
        await session.commit()
        title_id, rel_id = title.id, rel.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(f"/relationships/{rel_id}", json={"relationship_type": "남매"})
        assert r.status_code == 200
        assert r.json()["relationship_type"] == "남매"

        r = await client.delete(f"/relationships/{rel_id}")
        assert r.status_code == 200

        r = await client.get(f"/titles/{title_id}/relationships")
        assert r.json() == []


@pytest.mark.asyncio
async def test_confirm_chart_sets_status_confirmed(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    async with async_session() as session:
        title = Title(name="T", type="series", chart_extraction_status="review_needed")
        session.add(title)
        await session.commit()
        title_id = title.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/titles/{title_id}/chart/confirm")
        assert r.status_code == 200
        assert r.json()["chart_extraction_status"] == "confirmed"
