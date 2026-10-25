export const safeHref = (href) => {
    if (typeof href !== 'string' || href.length > 2048 || /[\s\\]/.test(href))
        return null;
    try {
        const u = new URL(href);
        if (u.protocol === 'http:' || u.protocol === 'https:')
            return u.username || u.password ? null : u.href;
        if (u.protocol === 'mailto:')
            return u.href;
    }
    catch { /* not a URL */ }
    return null;
};
//# sourceMappingURL=view.js.map