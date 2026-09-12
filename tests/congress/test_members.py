"""`members.py` — 위원회 · 의원 · 의원위원회의 순수 파싱."""

import pytest

import members


class Test의원_대수이력:
    def test_단일_대수는_그대로(self):
        값, 미해결 = members.parse_member(
            {
                "NAAS_CD": "GX38539O",
                "NAAS_NM": "강경숙",
                "GTELT_ERACO": "제22대",
                "PLPT_NM": "조국혁신당",
                "ELECD_NM": "비례대표",
            }
        )
        assert 값 == {
            "의원코드": "GX38539O",
            "이름": "강경숙",
            "정당": "조국혁신당",
            "선거구": "비례대표",
        }
        assert 미해결 == []

    def test_두_대수면_22대_자리의_값을_취한다(self):
        """S9. ⚠️ `PLPT_NM` 을 그대로 넣으면 정당명이 `'미래통합당/국민의힘'` 이 된다."""
        값, 미해결 = members.parse_member(
            {
                "NAAS_CD": "A1",
                "NAAS_NM": "강대식",
                "GTELT_ERACO": "제21대, 제22대",
                "PLPT_NM": "미래통합당/국민의힘",
                "ELECD_NM": "대구 동구을/대구 동구군위군을",
            }
        )
        assert 값["정당"] == "국민의힘"
        assert 값["선거구"] == "대구 동구군위군을"
        assert 미해결 == []

    def test_세_대수도_자리를_맞춘다(self):
        값, _ = members.parse_member(
            {
                "NAAS_CD": "A2",
                "NAAS_NM": "유동수",
                "GTELT_ERACO": "제20대, 제21대, 제22대",
                "PLPT_NM": "더불어민주당/더불어민주당/더불어민주당",
                "ELECD_NM": "인천 계양구갑/인천 계양구갑/인천 계양구갑",
            }
        )
        assert 값["정당"] == "더불어민주당"

    def test_마지막이_아니라_22대_자리를_집는다(self):
        """`GTELT_ERACO` 가 정렬돼 온다는 보장이 없다. **위치로 집는다.**"""
        값, _ = members.parse_member(
            {
                "NAAS_CD": "A3",
                "NAAS_NM": "가상",
                "GTELT_ERACO": "제22대, 제21대",
                "PLPT_NM": "국민의힘/미래통합당",
                "ELECD_NM": "서울 강남/서울 강남",
            }
        )
        assert 값["정당"] == "국민의힘"

    def test_22대가_마지막이면_조각_수가_어긋나도_마지막을_취한다(self):
        """실측: 박지원(5선) 이 대수 5개 / 선거구 조각 4개다.

        **22대는 아직 마지막 대수라(23대가 없다) 마지막 조각이 곧 22대 값이다** — 추측이
        아니라 구조에서 나오는 사실이다. 실제로 마지막 조각 '전남 해남군완도군진도군' 이
        박지원의 22대 선거구다. 이 규칙이 없으면 그 한 명이 영구히 빨간 게이트가 된다.
        """
        값, 미해결 = members.parse_member(
            {
                "NAAS_CD": "A4",
                "NAAS_NM": "박지원",
                "GTELT_ERACO": "제14대, 제18대, 제19대, 제20대, 제22대",
                "PLPT_NM": "A/B/C/D/E",
                "ELECD_NM": "전남 목포시/전남 목포시/전남 목포시/전남 해남군완도군진도군",
            }
        )
        assert 값["정당"] == "E"
        assert 값["선거구"] == "전남 해남군완도군진도군"
        assert 미해결 == []

    def test_22대가_마지막이_아니면_추측하지_않는다(self):
        """⚠️ 자리를 못 맞출 때 **가운데를 추측해서 채우지 마라.** 근거가 없고 틀려도 에러가 안 난다."""
        값, 미해결 = members.parse_member(
            {
                "NAAS_CD": "A7",
                "NAAS_NM": "가상",
                "GTELT_ERACO": "제22대, 제21대, 제20대",
                "PLPT_NM": "A/B/C",
                "ELECD_NM": "가/나",
            }
        )
        assert 값["선거구"] is None
        assert 미해결 == ["선거구"]

    def test_원천이_빈_값을_주면_NULL_이되_실패는_아니다(self):
        """⚠️ **우리가 고칠 수 없는 것을 원장에 남기지 마라.**

        조각 수는 맞는데 값이 빈 경우다(실측: 손솔·최혁진의 `ELECD_NM`). 원장에 남기면
        영구히 빨간 게이트가 되고, **고칠 수 없는 빨간불은 표 전체를 무시하게 만든다.**
        자리를 못 맞춘 것(위 테스트)과는 다른 상태다.
        """
        값, 미해결 = members.parse_member(
            {
                "NAAS_CD": "A6",
                "NAAS_NM": "손솔",
                "GTELT_ERACO": "제22대",
                "PLPT_NM": "진보당",
                "ELECD_NM": "",
            }
        )
        assert 값["선거구"] is None
        assert 값["정당"] == "진보당"
        assert 미해결 == []

    def test_22대가_없으면_거부한다(self):
        with pytest.raises(ValueError, match="제22대"):
            members.parse_member(
                {"NAAS_CD": "A5", "NAAS_NM": "가상", "GTELT_ERACO": "제21대", "PLPT_NM": "X"}
            )


class Test위원회목록:
    rows = [
        {"CLASS_NM": "국회본회의", "CMIT_NM": "국회본회의", "SUB_CMIT_NM": None},
        {"CLASS_NM": "상임위원회", "CMIT_NM": "법제사법위원회", "SUB_CMIT_NM": None},
        {"CLASS_NM": "상임위원회", "CMIT_NM": "법제사법위원회", "SUB_CMIT_NM": "법안심사제1소위원회"},
        {"CLASS_NM": "국정감사", "CMIT_NM": "법제사법위원회", "SUB_CMIT_NM": None},
        {"CLASS_NM": "상임위원회", "CMIT_NM": "농림축산식품해양수산위원회", "SUB_CMIT_NM": "청원심사소위원회"},
    ]

    def test_상위가_소위보다_먼저_나온다(self):
        """자기참조 FK 라 부모가 먼저 들어가야 한다."""
        목록 = members.parse_위원회목록(self.rows)
        순서 = [이름 for 이름, _ in 목록]
        assert 순서.index("법제사법위원회") < 순서.index("법제사법위원회 법안심사제1소위원회")
        assert 순서.index("농림축산식품해양수산위원회") < 순서.index(
            "농림축산식품해양수산위원회 청원심사소위원회"
        )

    def test_소위는_상위_공백_소위명으로_이어_붙인다(self):
        목록 = dict(members.parse_위원회목록(self.rows))
        assert 목록["법제사법위원회 법안심사제1소위원회"] == "법제사법위원회"
        assert 목록["법제사법위원회"] is None

    def test_같은_위원회가_CLASS_NM_별로_반복돼도_한_행이다(self):
        """⚠️ **`CLASS_NM` 을 위원회에 넣으면 안 되는 이유.** 법사위가 '상임위원회' 로도
        '국정감사' 로도 온다. 옛 프로젝트가 이걸 넣어 **상임위 17개 중 14개를 '국정감사'로
        굳혔다.** 여기서는 이름만 본다.
        """
        목록 = members.parse_위원회목록(self.rows)
        assert [이름 for 이름, _ in 목록].count("법제사법위원회") == 1

    def test_본회의도_위원회_행이다(self):
        """수집 범위 밖이지만 `위원회` 는 차원 테이블이라 이름이 있어야 조인이 안 깨진다."""
        assert "국회본회의" in dict(members.parse_위원회목록(self.rows))


class Test의원위원회:
    def test_의원코드로_잇는다(self):
        """⚠️ **이름으로 잇지 마라 — 22대에 박지원이 둘이다.**"""
        쌍 = members.parse_의원위원회(
            [
                {"DEPT_NM": "국회운영위원회", "MONA_CD": "HE08888N", "JOB_RES_NM": "위원"},
                {"DEPT_NM": "국회운영위원회", "MONA_CD": "3BC6890E", "JOB_RES_NM": "간사"},
                {"DEPT_NM": "법제사법위원회", "MONA_CD": "HE08888N", "JOB_RES_NM": "위원장"},
            ]
        )
        assert 쌍 == [
            ("HE08888N", "국회운영위원회"),
            ("3BC6890E", "국회운영위원회"),
            ("HE08888N", "법제사법위원회"),
        ]

    def test_MONA_CD_가_없는_행은_버린다(self):
        """실측 477/477 에 있지만, 없으면 FK 위반으로 트랜잭션이 통째로 죽는다."""
        쌍 = members.parse_의원위원회(
            [{"DEPT_NM": "국회운영위원회", "MONA_CD": None, "JOB_RES_NM": "위원"}]
        )
        assert 쌍 == []


# ── 정당은 현직 API 가 준다 ───────────────────────────────────────────────────
#
# `ALLNAMEMBER.PLPT_NM` 은 대수별 값이라 22대 **안에서** 옮긴 당적을 모른다 — 합당으로 사라진
# 위성정당(국민의미래·더불어민주연합)이 현직에 그대로 남고, 탈당한 의원이 옛 당에 세어진다.
# 매일 부르는 현직 API `nwvrqwxyaytdsfvhu` 의 `POLY_NM` 이 현재 정당이다. 전직은 그 API 에
# 없으므로 **마지막으로 확인한 값이 얼어 있고**, 언제 확인했는지가 `정당확인일` 이다.


def _오늘():
    from datetime import datetime

    import db

    return datetime.now(db.KST).strftime("%Y-%m-%d")


class 정당원천:
    """`members.collect` 이 쓰는 네 API. 현직 명단이 `{코드: 정당}` 으로 온다."""

    def __init__(self, 현직: dict, 전체=None):
        self.현직 = 현직
        self.전체 = 전체 if 전체 is not None else [f"A{i}" for i in range(10)]

    def all_pages(self, api, **kw):
        if api == members.위원회목록_API:
            return [{"CMIT_NM": "정무위원회"}]
        if api == members.현직_API:
            return [{"MONA_CD": c, "POLY_NM": p} for c, p in self.현직.items()]
        if api == members.의원전체_API:
            return [
                {"NAAS_CD": c, "NAAS_NM": f"의원{c}", "GTELT_ERACO": "제22대",
                 "PLPT_NM": "국민의미래", "ELECD_NM": "비례대표"}
                for c in self.전체
            ]
        if api == members.위원명단_API:
            return []
        raise AssertionError(f"모르는 API: {api}")


def _정당(conn, 코드):
    return tuple(conn.execute(
        "SELECT 정당, 정당확인일, 현직여부 FROM 의원 WHERE 의원코드=?", (코드,)
    ).fetchone())


class Test현직정당:
    def test_parse_현직은_코드별_현재_정당을_준다(self):
        assert members.parse_현직(
            [{"MONA_CD": "A1", "POLY_NM": "국민의힘"}, {"MONA_CD": "A2", "POLY_NM": "무소속"}]
        ) == {"A1": "국민의힘", "A2": "무소속"}

    def test_정당이_빈_행은_모르는_것이지_무소속이_아니다(self):
        """빈 값을 그대로 쓰면 옛 정당을 NULL 로 덮는다. 모르면 손대지 않는다."""
        assert members.parse_현직(
            [{"MONA_CD": "A1", "POLY_NM": ""}, {"MONA_CD": "A2"}, {"MONA_CD": "", "POLY_NM": "x"}]
        ) == {}

    def test_현직의_정당은_현직_API_값이고_확인일이_찍힌다(self, conn):
        """유용원: `PLPT_NM` 국민의미래(당선 당시) · `POLY_NM` 국민의힘(합당 뒤)."""
        전원 = {f"A{i}": "국민의힘" for i in range(10)}
        members.collect(conn, 정당원천(전원), 로그=lambda _: None)
        assert _정당(conn, "A0") == ("국민의힘", _오늘(), 1)
        assert conn.execute(
            "SELECT COUNT(*) FROM 의원 WHERE 정당='국민의미래'").fetchone()[0] == 0

    def test_한_번도_확인_못_한_전직은_당선_당시_정당에_확인일_NULL(self, conn):
        """현직 API 에 없는 사람은 확인할 원천이 없다. `PLPT_NM` 이 남고 확인일은 NULL —
        「확인한 적 없다」가 데이터로 보인다."""
        현직 = {f"A{i}": "국민의힘" for i in range(9)}       # A9 는 이미 떠났다
        members.collect(conn, 정당원천(현직), 로그=lambda _: None)
        assert _정당(conn, "A9") == ("국민의미래", None, 0)

    def test_떠난_의원은_마지막_확인값이_얼어_있다(self, conn):
        """⚠️ **전직을 무소속으로 채우지 마라.** 탈당해서 떠난 것이 아니라 사퇴·상실로 떠난
        사람이 대부분이고, 마지막 확인값과 그 날짜가 정직한 답이다."""
        전원 = {f"A{i}": "조국혁신당" for i in range(10)}
        members.collect(conn, 정당원천(전원), 로그=lambda _: None)
        확인일 = _정당(conn, "A9")[1]
        members.collect(conn, 정당원천({k: v for k, v in 전원.items() if k != "A9"}),
                        로그=lambda _: None)
        assert _정당(conn, "A9") == ("조국혁신당", 확인일, 0)

    def test_축소_가드가_정당_갱신도_함께_멈춘다(self, conn):
        """현직 명단이 반토막인 날 `현직여부` 만 지키고 정당을 갱신하면, 그날 명단에 남은
        절반만 새 정당이 되어 **한 표 안에 두 시점의 정당이 섞인다.**"""
        전원 = {f"A{i}": "국민의미래" for i in range(10)}
        members.collect(conn, 정당원천(전원), 로그=lambda _: None)
        반토막 = {f"A{i}": "국민의힘" for i in range(5)}
        members.collect(conn, 정당원천(반토막), 로그=lambda _: None)
        assert conn.execute(
            "SELECT COUNT(*) FROM 의원 WHERE 정당='국민의힘'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM 의원 WHERE 현직여부=1").fetchone()[0] == 10

    def test_ALLNAMEMBER_의_정당은_새_행에만_쓴다(self, conn):
        """`PLPT_NM` 을 매일 덮으면 현직 API 가 준 현재 정당이 다음 날 당선 당시 정당으로
        되돌아간다. 새 행(INSERT)에서만 초깃값이다."""
        전원 = {f"A{i}": "국민의힘" for i in range(10)}
        members.collect(conn, 정당원천(전원), 로그=lambda _: None)

        class 현직없음(정당원천):
            def all_pages(self, api, **kw):
                if api == members.현직_API:
                    return [{"MONA_CD": c} for c in 전원]     # 명단은 그대로, 정당만 안 온다
                return super().all_pages(api, **kw)

        members.collect(conn, 현직없음(전원), 로그=lambda _: None)
        assert _정당(conn, "A0")[0] == "국민의힘"
