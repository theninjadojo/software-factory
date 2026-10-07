"""The smallest Excel (.xlsx) round trip the Tests register needs, on the standard library: write one sheet of text cells, and read
the first sheet of a workbook back as rows of text.

Written cells are inline strings, never formulas, so a value like =HYPERLINK(...) shows as text in Excel. Read workbooks are
untrusted: the archive's size is capped before anything is unpacked, and XML with a DOCTYPE (entity expansion) is refused."""
import io
import re
import zipfile
import zlib
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

MAX_UNPACKED = 20_000_000
NOT_XML = re.compile(r"[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class XlsxError(ValueError):
    pass


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write(rows: list[list], sheet: str = "Sheet1") -> bytes:
    """A workbook of one sheet; the first row is bold and frozen, as a header."""
    lines = []
    for i, row in enumerate(rows, 1):
        bold = ' s="1"' if i == 1 else ""
        cells = "".join(f'<c r="{_col(j)}{i}" t="inlineStr"{bold}><is><t xml:space="preserve">'
                        f'{escape(NOT_XML.sub("", "" if v is None else str(v)))}</t></is></c>' for j, v in enumerate(row, 1))
        lines.append(f'<row r="{i}">{cells}</row>')
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 f'<sheetData>{"".join(lines)}</sheetData></worksheet>')
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return out.getvalue()


def is_xlsx(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def _xml(z: zipfile.ZipFile, name: str):
    try:
        raw = z.read(name)
    except KeyError:
        return None
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError):
        raise XlsxError("The workbook is damaged or encrypted.") from None
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise XlsxError("The workbook has XML this importer does not read.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise XlsxError("The workbook is damaged.") from None


def _text(node) -> str:
    """A string item: plain <t>, or rich text runs (<r><t>), without phonetic hints (<rPh>)."""
    if node is None:
        return ""
    t = node.find("m:t", NS)
    if t is not None:
        return t.text or ""
    return "".join(r.findtext("m:t", "", NS) for r in node.findall("m:r", NS))


def _index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read(data: bytes, max_rows: int = 100_000) -> list[list[str]]:
    """The first sheet of a workbook as rows of text (empty cells are ''). Numbers come back as Excel stores them ("3", "2.5").
    Raises XlsxError with a message safe to show."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, EOFError):
        raise XlsxError("The file is not an Excel workbook (.xlsx).") from None
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
            raise XlsxError("The workbook is too large once unpacked.")
        book, rels = _xml(z, "xl/workbook.xml"), _xml(z, "xl/_rels/workbook.xml.rels")
        if book is None:
            raise XlsxError("The file is not an Excel workbook (.xlsx).")
        first = book.find("m:sheets/m:sheet", NS)
        target = "worksheets/sheet1.xml"
        if first is not None and rels is not None:
            rid = first.get(REL)
            for r in rels:
                if r.get("Id") == rid:
                    target = r.get("Target", target)
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        shared_xml = _xml(z, "xl/sharedStrings.xml")
        shared = [_text(si) for si in shared_xml.findall("m:si", NS)] if shared_xml is not None else []
        sheet = _xml(z, path)
        if sheet is None:
            raise XlsxError("The workbook has no sheet.")
    rows = []
    for row in sheet.iterfind("m:sheetData/m:row", NS):
        if len(rows) >= max_rows:
            raise XlsxError(f"More than {max_rows:,} rows.")
        cells: dict[int, str] = {}
        for i, c in enumerate(row.findall("m:c", NS)):
            col = _index(c.get("r", "")) if c.get("r") else i
            kind, v = c.get("t", "n"), c.findtext("m:v", None, NS)
            if kind == "s":
                val = shared[int(v)] if v is not None and v.isdigit() and int(v) < len(shared) else ""
            elif kind == "inlineStr":
                val = _text(c.find("m:is", NS))
            elif kind == "b":
                val = "TRUE" if v == "1" else "FALSE"
            else:
                val = v or ""
                if kind == "n" and re.fullmatch(r"-?\d+\.0+", val):
                    val = val.split(".")[0]
            if 0 <= col < 1000:
                cells[col] = val
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows
