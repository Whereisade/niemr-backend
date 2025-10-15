from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string

def render_html(template: str, ctx: dict) -> str:
    return render_to_string(template, ctx)

def try_render_pdf(html: str, *, filename: str = "report.pdf"):
    """
    Return (HttpResponse or None). If WeasyPrint is not available, return None.
    """
    if not getattr(settings, "REPORTS_ENABLE_PDF", True):
        return None
    try:
        from weasyprint import HTML, CSS
    except Exception:
        return None

    pdf = HTML(string=html, base_url="/").write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp
