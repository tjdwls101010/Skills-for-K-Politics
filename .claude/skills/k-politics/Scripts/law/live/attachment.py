"""첨부(PDF/HWP)는 텍스트가 빈 서식류의 폴백이다.

⚠️ HWP 제어문자는 1 또는 8 UTF-16 단위다. 제어 payload를 글자로 읽지 않는다.
"""

from __future__ import annotations

import io
import struct
import zlib


def 첨부텍스트(자료):
    if 자료.startswith(b"%PDF"):
        from pypdf import PdfReader
        return "\n".join(쪽.extract_text() or "" for 쪽 in PdfReader(io.BytesIO(자료)).pages)
    if not 자료.startswith(bytes.fromhex("D0 CF 11 E0")):
        raise ValueError("PDF 또는 HWP 5.x 첨부가 아니다")
    import olefile
    with olefile.OleFileIO(io.BytesIO(자료)) as 문서:
        if 문서.exists("PrvText"):
            return 문서.openstream("PrvText").read().decode("utf-16le").rstrip("\x00")
        압축 = bool(문서.openstream("FileHeader").read()[36] & 1)
        줄들 = []
        구역들 = [p for p in 문서.listdir() if len(p) == 2 and p[0] == "BodyText" and p[1].startswith("Section")]
        for 경로 in sorted(구역들, key=lambda p: int(p[1][7:])):
            내용 = 문서.openstream(경로).read()
            if 압축:
                내용 = zlib.decompress(내용, -15)
            위치 = 0
            while 위치 < len(내용):
                머리, = struct.unpack_from("<I", 내용, 위치)
                위치 += 4
                태그, 크기 = 머리 & 0x3ff, 머리 >> 20
                if 크기 == 0xfff:
                    크기, = struct.unpack_from("<I", 내용, 위치)
                    위치 += 4
                if 위치 + 크기 > len(내용):
                    raise ValueError("HWP 텍스트 레코드가 잘렸다")
                if 태그 == 67:
                    레코드 = 내용[위치:위치 + 크기]
                    글, i = [], 0
                    while i < len(레코드):
                        코드, = struct.unpack_from("<H", 레코드, i)
                        if 코드 >= 32:
                            글.append(레코드[i:i + 2])
                        elif 코드 in (9, 10, 13):
                            글.append(("\t" if 코드 == 9 else "\n").encode("utf-16le"))
                        i += 16 if 코드 in (*range(1, 10), *range(11, 13), *range(14, 24)) else 2
                    줄들.append(b"".join(글).decode("utf-16le"))
                위치 += 크기
        return "\n".join(줄들)



