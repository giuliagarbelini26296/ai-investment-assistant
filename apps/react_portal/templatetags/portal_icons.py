from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()
PATHS = {
    'overview': '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    'wallet': '<path d="M20 8V5a2 2 0 0 0-2-2H5a3 3 0 0 0 0 6h15v11H5a3 3 0 0 1-3-3V6"/><path d="M20 12h-5v5h5"/>',
    'market': '<path d="M3 3v18h18M7 14v3M12 10v7M17 6v11M5 10l6-5 5 2 5-5"/>',
    'radio': '<circle cx="12" cy="12" r="2"/><path d="M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14M8 8a6 6 0 0 0 0 8M16 8a6 6 0 0 1 0 8"/>',
    'bell': '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    'news': '<rect x="4" y="3" width="17" height="18" rx="2"/><path d="M4 8H2v11a2 2 0 0 0 2 2M8 7h9M8 11h9M8 15h3M14 15h3"/>',
    'upload': '<path d="M12 16V3M7 8l5-5 5 5M3 15v5a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-5"/>',
    'assets': '<path d="M12 3v18M17 7H9a3 3 0 0 0 0 6h6a3 3 0 0 1 0 6H6"/>',
    'layers': '<path d="m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5"/>',
    'shield': '<path d="M12 3 3 7v6c0 5 9 9 9 9s9-4 9-9V7l-9-4Z"/><path d="m8 12 3 3 5-6"/>',
    'menu': '<path d="M3 6h18M3 12h18M3 18h18"/>',
    'chevron': '<path d="m9 5 7 7-7 7"/>',
    'close': '<path d="m6 6 12 12M6 18 18 6"/>',
}


@register.simple_tag
def portal_icon(name):
    # Only constant paths from this allowlist become markup.
    return format_html('<svg width="19" height="19" viewBox="0 0 24 24" fill="none" '
                       'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
                       'stroke-linejoin="round" aria-hidden="true">{}</svg>',
                       mark_safe(PATHS.get(name, PATHS['layers'])))
