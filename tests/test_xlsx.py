import io
import sqlite3
import unittest
import zipfile

from factory import scenarios as SC
from factory import xlsx

REPO = "o/r"
M = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def excel_style(sheet_rows: str, shared: str = "", sheet_path: str = "xl/worksheets/sheet7.xml", extra: dict | None = None) -> bytes:
    """A workbook laid out the way Excel saves one: shared strings, a sheet that is not sheet1, cells with gaps."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {M} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                                      '<sheets><sheet name="Mine" sheetId="1" r:id="rId3"/></sheets></workbook>')
        if "xl/_rels/workbook.xml.rels" not in (extra or {}):
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                     f'<Relationship Id="rId3" Target="{sheet_path[3:]}"/></Relationships>')
        if "xl/sharedStrings.xml" not in (extra or {}):
            z.writestr("xl/sharedStrings.xml", f"<sst {M}>{shared}</sst>")
        z.writestr(sheet_path, f"<worksheet {M}><sheetData>{sheet_rows}</sheetData></worksheet>")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return out.getvalue()


class TestXlsx(unittest.TestCase):
    def test_written_workbook_reads_back_the_same(self):
        rows = [["id", "title"], ["TS-1", "=HYPERLINK(\"x\")"], ["", "line one\nline two  "], [3, None], ["bad\x01char", "日本語 🚀"]]
        got = xlsx.read(xlsx.write(rows))
        self.assertEqual(got, [["id", "title"], ["TS-1", '=HYPERLINK("x")'], ["", "line one\nline two  "], ["3", ""], ["badchar", "日本語 🚀"]])

    def test_written_cells_are_text_never_formulas(self):
        with zipfile.ZipFile(io.BytesIO(xlsx.write([["=1+1"]]))) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn("<f>", sheet)
        self.assertIn('t="inlineStr"', sheet)

    def test_reads_shared_strings_rich_text_numbers_booleans_and_gaps(self):
        shared = "<si><t>title</t></si><si><r><t>Pay </t></r><r><t>by card</t></r><rPh><t>x</t></rPh></si><si><t>id</t></si>"
        rows = ('<row r="1"><c r="A1" t="s"><v>2</v></c><c r="C1" t="s"><v>0</v></c></row>'
                '<row r="2"><c r="A2"><v>12.0</v></c><c r="B2" t="b"><v>1</v></c><c r="C2" t="s"><v>1</v></c></row>'
                '<row r="3"><c r="C3" t="s"><v>99</v></c></row>')
        self.assertEqual(xlsx.read(excel_style(rows, shared)), [["id", "", "title"], ["12", "TRUE", "Pay by card"], ["", "", ""]])

    def test_hostile_or_broken_files_are_refused(self):
        with self.assertRaises(xlsx.XlsxError):
            xlsx.read(b"PK\x03\x04 not really a zip")
        bomb = excel_style("", extra={"xl/sharedStrings.xml": '<!DOCTYPE x [<!ENTITY a "aaaa">]><sst/>'})
        with self.assertRaises(xlsx.XlsxError):
            xlsx.read(bomb)
        big = io.BytesIO()
        with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("xl/workbook.xml", "<a/>")
            z.writestr("pad", b"\0" * (xlsx.MAX_UNPACKED + 1))
        with self.assertRaises(xlsx.XlsxError):
            xlsx.read(big.getvalue())
        with self.assertRaises(xlsx.XlsxError):
            xlsx.read(xlsx.write([["a"]]).replace(b"sheet1.xml", b"sheet9.xml", 1))        # damaged: the names no longer agree
        with self.assertRaisesRegex(xlsx.XlsxError, "no sheet"):
            xlsx.read(excel_style("", extra={"xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                                         '<Relationship Id="rId3" Target="worksheets/gone.xml"/></Relationships>'}))


class TestScenarioWorkbooks(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        SC.ensure_tables(self.db)

    def test_an_unchanged_excel_export_imports_as_no_change(self):
        SC.create(self.db, REPO, {"title": "=cmd", "feature": "F", "steps": "1. a\n2. b", "pw_test": "t.py::test_a"})
        SC.add_result(self.db, 1, "fail", "bad", "Sam")
        data = SC.export_xlsx(SC.listing(self.db, REPO))
        rows = SC.parse_upload(data)
        self.assertEqual(rows[0]["title"], "=cmd")
        self.assertEqual(rows[0]["revision"], "1")
        self.assertEqual([i["action"] for i in SC.plan(self.db, REPO, rows)], ["same"])

    def test_a_sheet_from_excel_imports_new_rows(self):
        shared = "<si><t>Title</t></si><si><t>Feature</t></si><si><t>Sign in</t></si><si><t>Login</t></si><si><t>Sign out</t></si>"
        rows = ('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
                '<row r="4"><c r="A4" t="s"><v>4</v></c></row>')
        got = SC.parse_upload(excel_style(rows, shared))
        self.assertEqual([(r["title"], r["feature"]) for r in got], [("Sign in", "Login"), ("Sign out", "")])
        self.assertEqual(SC.apply(self.db, REPO, got, set())["created"], 2)

    def test_csv_still_goes_to_the_csv_reader_and_bad_sheets_explain_themselves(self):
        self.assertEqual(SC.parse_upload(b"title\nA\n")[0]["title"], "A")
        with self.assertRaisesRegex(ValueError, "title column"):
            SC.parse_upload(xlsx.write([["name"], ["x"]]))
        with self.assertRaisesRegex(ValueError, "1 MB"):
            SC.parse_upload(b"PK\x03\x04" + b"\0" * SC.MAX_CSV)


if __name__ == "__main__":
    unittest.main()
