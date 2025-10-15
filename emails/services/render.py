from string import Template as StrTemplate
from emails.models import Template

def render_template(code: str, data: dict) -> tuple[str, str, str]:
    t = Template.objects.get(code=code)
    sub = StrTemplate(t.subject).safe_substitute(data or {})
    html = StrTemplate(t.html).safe_substitute(data or {})
    text = StrTemplate(t.text or "").safe_substitute(data or {})
    return sub, html, text
