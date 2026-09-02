"""Generates the adversarial PDF corpus into eval/adversarial/corpus/.

Deterministic: running this twice produces byte-identical files. The corpus
is gitignored (see eval/adversarial/.gitignore) and regenerated on demand:

    .venv/bin/python eval/adversarial/generate.py

Every case is hand-built at the byte level (no reportlab/pypdf writer) so the
structure can be malformed on purpose: bad xref, cyclic references, oversized
declared lengths, and so on. Nothing here fetches anything from the network or
targets a specific parser CVE -- these are stress shapes (huge counts, bad
geometry, broken structure), not exploits.
"""
import zlib
from pathlib import Path
import json

CORPUS_DIR = Path(__file__).parent / "corpus"


# --------------------------------------------------------------------------
# A minimal, deliberately low-level PDF builder. It writes objects in object
# order, computes real xref offsets, and points /Root at whichever object the
# caller names. Nothing here validates the object bodies -- callers can (and
# do) hand it garbage on purpose.
# --------------------------------------------------------------------------
class MiniPDF:
    def __init__(self):
        self._objects = {}  # obj number -> body bytes (no "N 0 obj"/"endobj" wrapper)

    def add(self, num, body: bytes):
        assert num not in self._objects, f"object {num} already defined"
        self._objects[num] = body
        return num

    def build(self, root_num: int, header=b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n") -> bytes:
        out = bytearray(header)
        offsets = {}
        for num in sorted(self._objects):
            offsets[num] = len(out)
            out += f"{num} 0 obj\n".encode("latin-1")
            out += self._objects[num]
            if not bytes(out).endswith(b"\n"):
                out += b"\n"
            out += b"endobj\n"
        xref_offset = len(out)
        max_num = max(self._objects)
        out += f"xref\n0 {max_num + 1}\n".encode("latin-1")
        out += b"0000000000 65535 f \n"
        for num in range(1, max_num + 1):
            off = offsets.get(num, 0)
            out += f"{off:010d} 00000 n \n".encode("latin-1")
        out += b"trailer\n"
        out += f"<< /Size {max_num + 1} /Root {root_num} 0 R >>\n".encode("latin-1")
        out += b"startxref\n"
        out += f"{xref_offset}\n".encode("latin-1")
        out += b"%%EOF\n"
        return bytes(out)


def stream_obj(pdf_dict: bytes, data: bytes) -> bytes:
    """Build an object body '<< ... /Length N >>\\nstream\\n<data>\\nendstream'."""
    return (pdf_dict[:-2].rstrip() + f" /Length {len(data)} >>\nstream\n".encode("latin-1")
            + data + b"\nendstream")


def flate(data: bytes) -> bytes:
    return zlib.compress(data, 9)


HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _simple_one_page_pdf(media_box: str, content: bytes, extra_page_dict: bytes = b"",
                          flate_content=True) -> bytes:
    """A single-page PDF with one content stream. Used for the geometry cases."""
    pdf = MiniPDF()
    if flate_content:
        body = stream_obj(b"<< /Filter /FlateDecode >>", flate(content))
    else:
        body = stream_obj(b"<< >>", content)
    pdf.add(4, body)
    pdf.add(5, HELVETICA)
    pdf.add(3, (f"<< /Type /Page /Parent 2 0 R /MediaBox {media_box} "
                f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >>"
                ).encode("latin-1") + extra_page_dict + b" >>")
    pdf.add(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    pdf.add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    return pdf.build(root_num=1)


# --------------------------------------------------------------------------
# Case generators. Each returns (filename, bytes, readme_line).
# --------------------------------------------------------------------------

def case_decompression_bomb():
    """Flate stream expands ~10,000x: a few hundred KB on disk, gigabytes decompressed.
    Attacks naive "decompress everything into memory" filter handling."""
    # Highly repetitive payload -> extreme Flate compression ratio.
    payload = b"0" * (300 * 1024 * 1024)  # 300 MB of the same byte, decompressed size
    content = flate(payload)
    pdf = MiniPDF()
    pdf.add(4, stream_obj(b"<< /Filter /FlateDecode >>", content))
    pdf.add(5, HELVETICA)
    pdf.add(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
    pdf.add(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    pdf.add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    return ("decompression_bomb.pdf", pdf.build(root_num=1),
            "1 page, one Flate content stream compressing a 300 MB run of zero bytes "
            "into a few hundred KB on disk -- attacks unbounded-decompression handling.")


def case_page_flood():
    """5000 pages sharing one tiny content stream. Attacks per-page setup cost times 5000."""
    pdf = MiniPDF()
    content_num = pdf.add(3, stream_obj(b"<< /Filter /FlateDecode >>", flate(b"")))
    font_num = pdf.add(4, HELVETICA)
    n_pages = 5000
    first_page_num = 5
    kids = []
    for i in range(n_pages):
        num = first_page_num + i
        kids.append(f"{num} 0 R")
        pdf.add(num, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                      f"/Contents {content_num} 0 R "
                      f"/Resources << /Font << /F1 {font_num} 0 R >> >> >>").encode("latin-1"))
    pdf.add(2, f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n_pages} >>".encode("latin-1"))
    pdf.add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    return ("page_flood.pdf", pdf.build(root_num=1),
            f"{n_pages} pages, each an empty page sharing one content stream -- "
            "attacks per-page processing cost times page count, over the 30-page product limit.")


def case_rect_flood():
    """100,000 tiny filled rects on one page. Attacks the O(rects^2) cell-recovery loop
    in engine/detect/rules.py (grid_cells), which is guarded at 2000 rules/page."""
    n = 100_000
    lines = []
    for i in range(n):
        x = i % 600
        y = (i // 600) % 700
        lines.append(f"{x} {y} 1 1 re f")
    content = ("\n".join(lines) + "\n").encode("latin-1")
    return ("rect_flood.pdf",
             _simple_one_page_pdf("[0 0 612 792]", content),
             f"1 page, {n} tiny 1x1pt filled rects -- attacks the quadratic "
             "grid_cells() cell-recovery loop; should be stopped by the "
             "2000-rects/page guard or killed by the CPU cap.")


def case_char_flood():
    """500,000 characters on one page via a single Tj string. Attacks per-char handling."""
    n = 500_000
    text = b"A" * n
    content = b"BT /F1 8 Tf 1 0 0 1 10 700 Tm (" + text + b") Tj ET\n"
    return ("char_flood.pdf",
            _simple_one_page_pdf("[0 0 612 792]", content),
            f"1 page, a single text-show operator with {n} characters -- attacks "
            "per-character extraction and word/label reconstruction cost.")


def case_deep_nesting():
    """A chain of 2000 nested Form XObjects, each invoking the next via Do.
    Attacks recursive content-stream interpretation (stack depth / time)."""
    depth = 2000
    pdf = MiniPDF()
    font_num = pdf.add(4, HELVETICA)
    first_xobj_num = 5000
    xobj_nums = [first_xobj_num + i for i in range(depth)]
    for i in range(depth):
        num = xobj_nums[i]
        if i == depth - 1:
            body = b"1 0 0 RG 0 0 10 10 re S\n"
            resources = b"<< >>"
        else:
            nxt = xobj_nums[i + 1]
            body = f"/X{nxt} Do\n".encode("latin-1")
            resources = f"<< /XObject << /X{nxt} {nxt} 0 R >> >>".encode("latin-1")
        obj_dict = (b"<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] "
                    b"/Resources " + resources + b" >>")
        pdf.add(num, stream_obj(obj_dict, body))
    root_xobj = xobj_nums[0]
    page_content = f"/X{root_xobj} Do\n".encode("latin-1")
    pdf.add(3, stream_obj(b"<< >>", page_content))
    pdf.add(2, b"<< /Type /Pages /Kids [1000 0 R] /Count 1 >>")
    pdf.add(1000, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                   f"/Contents 3 0 R /Resources << /Font << /F1 {font_num} 0 R >> "
                   f"/XObject << /X{root_xobj} {root_xobj} 0 R >> >> >>").encode("latin-1"))
    pdf.add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    return ("deep_nesting.pdf", pdf.build(root_num=1),
            f"1 page, a chain of {depth} nested Form XObjects each calling the next "
            "via Do -- attacks recursive content-stream interpretation (stack depth).")


def case_cyclic_references():
    """Two Form XObjects whose Do calls invoke each other: A -> B -> A -> ... forever.
    Attacks the interpreter's (lack of) cycle detection when walking XObjects."""
    pdf = MiniPDF()
    font_num = pdf.add(4, HELVETICA)
    # Object 10 (X_A) draws and calls X_B (11); object 11 (X_B) draws and calls X_A (10).
    pdf.add(10, stream_obj(
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] "
        b"/Resources << /XObject << /XB 11 0 R >> >> >>",
        b"/XB Do\n"))
    pdf.add(11, stream_obj(
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] "
        b"/Resources << /XObject << /XA 10 0 R >> >> >>",
        b"/XA Do\n"))
    pdf.add(3, stream_obj(b"<< >>", b"/XA Do\n"))
    pdf.add(2, b"<< /Type /Pages /Kids [1000 0 R] /Count 1 >>")
    pdf.add(1000, (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                   b"/Contents 3 0 R /Resources << /Font << /F1 " +
                   str(font_num).encode() + b" 0 R >> "
                   b"/XObject << /XA 10 0 R /XB 11 0 R >> >> >>"))
    pdf.add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    return ("cyclic_references.pdf", pdf.build(root_num=1),
            "1 page, two Form XObjects that call each other (A -> B -> A -> ...) "
            "forever -- attacks missing cycle detection while walking XObjects; "
            "should be killed by the CPU cap if the interpreter doesn't guard it.")


def case_encrypted():
    """Password-protected with RC4/AES via pypdf; no password supplied to the detector."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password="hunter2", owner_password="hunter2-owner")
    out = CORPUS_DIR / "encrypted.pdf"
    with open(out, "wb") as fh:
        writer.write(fh)
    data = out.read_bytes()
    return ("encrypted.pdf", data,
            "1 blank page, encrypted with a user password that is never supplied "
            "to the detector -- attacks the encrypted-without-key path.")


def case_truncated():
    """A structurally valid small PDF, cut off mid-object (no trailer/xref survives)."""
    content = b"BT /F1 12 Tf 72 700 Td (Hello, form.) Tj ET\n"
    full = _simple_one_page_pdf("[0 0 612 792]", content, flate_content=False)
    cut = full[: int(len(full) * 0.55)]
    return ("truncated.pdf", cut,
            "A valid 1-page PDF truncated at 55% of its length, mid-object, "
            "before any xref/trailer survives -- attacks incomplete-file handling.")


def case_not_a_pdf():
    """An HTML file with a .pdf extension and no PDF header at all."""
    html = (b"<!doctype html>\n<html><body><h1>This is not a PDF.</h1>"
            b"<p>It has a .pdf extension and nothing else in common with one.</p>"
            b"</body></html>\n")
    return ("not_a_pdf.pdf", html,
            "An HTML document saved with a .pdf extension -- attacks "
            "extension-based trust / missing %PDF-header check.")


def case_empty_file():
    """Zero bytes."""
    return ("empty_file.pdf", b"",
            "Zero-byte file -- attacks missing empty-input handling.")


def case_zero_size_page():
    """A single page whose MediaBox is 0x0."""
    return ("zero_size_page.pdf",
            _simple_one_page_pdf("[0 0 0 0]", b""),
            "1 page with MediaBox [0 0 0 0] -- attacks division-by-zero / "
            "degenerate-geometry handling when computing page-relative coordinates.")


def case_huge_page():
    """A single page 200 inches square (200 * 72 = 14400 pt)."""
    return ("huge_page.pdf",
            _simple_one_page_pdf("[0 0 14400 14400]", b""),
            "1 page with MediaBox [0 0 14400 14400] (200in square) -- attacks "
            "assumptions that page dimensions are bounded to normal paper sizes.")


def case_inverted_mediabox():
    """MediaBox with x1 < x0 and y1 < y0 (negative width/height)."""
    return ("inverted_mediabox.pdf",
            _simple_one_page_pdf("[600 800 0 0]", b""),
            "1 page with MediaBox [600 800 0 0] (x1<x0, y1<y0, negative "
            "width/height) -- attacks unchecked width/height sign assumptions.")


def case_javascript_openaction():
    """/OpenAction runs /JavaScript on open, plus a /Names /JavaScript entry.
    Tests that the detector never executes or propagates it -- detect() must
    never run embedded script; it should be inert dead data to a PDF-text reader."""
    pdf = MiniPDF()
    pdf.add(4, stream_obj(b"<< /Filter /FlateDecode >>", flate(b"")))
    pdf.add(5, HELVETICA)
    pdf.add(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
               b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
    pdf.add(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    js = b"app.alert('formfill-adversarial-test'); this.exportDataObject();"
    pdf.add(6, b"<< /Type /Action /S /JavaScript /JS (" + js + b") >>")
    pdf.add(7, b"<< /Names [(adversarial-test) 6 0 R] >>")
    pdf.add(8, b"<< /JavaScript 7 0 R >>")
    pdf.add(1, b"<< /Type /Catalog /Pages 2 0 R /OpenAction 6 0 R /Names 8 0 R >>")
    return ("javascript_openaction.pdf", pdf.build(root_num=1),
            "1 page with /OpenAction pointing at a /JavaScript action, plus a "
            "/Names /JavaScript entry -- tests that the detector never executes "
            "or propagates embedded script.")


GENERATORS = [
    case_decompression_bomb,
    case_page_flood,
    case_rect_flood,
    case_char_flood,
    case_deep_nesting,
    case_encrypted,
    case_truncated,
    case_not_a_pdf,
    case_empty_file,
    case_zero_size_page,
    case_huge_page,
    case_inverted_mediabox,
    case_cyclic_references,
    case_javascript_openaction,
]


def _count_pages(data: bytes):
    """Best-effort page count via pypdf, for the manifest's sanity checks.
    Returns None for files that are not readable as valid PDFs at all --
    that is expected for empty_file/not_a_pdf/truncated/encrypted."""
    import io
    import pypdf

    try:
        return len(pypdf.PdfReader(io.BytesIO(data), strict=False).pages)
    except Exception:
        return None


def main():
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    readme_lines = []
    manifest = []
    for gen in GENERATORS:
        name, data, readme_line = gen()
        path = CORPUS_DIR / name
        path.write_bytes(data)
        readme_lines.append(f"- `{name}`: {readme_line}")
        manifest.append({
            "file": name,
            "size_bytes": len(data),
            "description": readme_line,
            "expected_pages": _count_pages(data),
        })
        print(f"wrote {name} ({len(data)} bytes)")

    (CORPUS_DIR / "README.md").write_text(
        "# Adversarial corpus\n\n"
        "Generated by `eval/adversarial/generate.py`. Gitignored; regenerate on demand.\n\n"
        + "\n".join(readme_lines) + "\n"
    )
    (CORPUS_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n{len(manifest)} files written to {CORPUS_DIR}")


if __name__ == "__main__":
    main()
