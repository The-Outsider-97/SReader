export class ApiError extends Error {
    status;
    code;
    details;
    constructor(status, body) {
        super(body.message);
        this.status = status;
        this.code = body.code;
        this.details = body.details ?? {};
    }
}
export class Api {
    doFetch;
    /** Reports whether the person's system prefers dark colours; sent with every request so "system" themes resolve correctly. */
    dark = () => false;
    constructor(doFetch = (i, init) => fetch(i, init)) {
        this.doFetch = doFetch;
    }
    async request(method, path, body, extra = {}, keepalive = false) {
        const headers = { ...extra };
        const init = { method, headers, credentials: 'same-origin', keepalive };
        if (this.dark())
            headers['X-Reader-Dark'] = '1';
        if (method !== 'GET')
            headers['X-SLAI-Reader'] = '1';
        if (body instanceof ArrayBuffer || body instanceof Uint8Array || (typeof Blob !== 'undefined' && body instanceof Blob)) {
            init.body = body;
        }
        else if (body !== undefined) {
            headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(body);
        }
        let res;
        try {
            res = await this.doFetch(path, init);
        }
        catch {
            throw new ApiError(0, { code: 'offline', message: 'Reader could not be reached. Is it still running?' });
        }
        const text = await res.text();
        let json = null;
        try {
            json = text ? JSON.parse(text) : null;
        }
        catch {
            json = null;
        }
        if (!res.ok || !json || json.ok === false) {
            throw new ApiError(res.status, json?.error ?? { code: 'error', message: `The request failed (${res.status}).` });
        }
        return json.data;
    }
    get(path) { return this.request('GET', path); }
    post(path, body = {}) { return this.request('POST', path, body); }
    put(path, body = {}, keepalive = false) { return this.request('PUT', path, body, {}, keepalive); }
    patch(path, body = {}) { return this.request('PATCH', path, body); }
    del(path) { return this.request('DELETE', path); }
    upload(name, data, password) {
        return this.request('POST', `/api/upload?name=${encodeURIComponent(name)}`, data, password ? { 'X-Reader-Password': password } : {});
    }
}
export const qs = (params) => {
    const parts = [];
    for (const [k, v] of Object.entries(params))
        if (v !== undefined && v !== null && v !== '')
            parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
    return parts.length ? `?${parts.join('&')}` : '';
};
//# sourceMappingURL=api.js.map