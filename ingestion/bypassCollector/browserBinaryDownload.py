"""Bounded same-origin binary download inside an already-open browser session."""

import base64
import json
from urllib.parse import urlsplit

from ingestion.urlResolver.htmlImageResolver import http_url


BINARY_FETCH_EXPRESSION = r"""
(async function(options) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeoutMs);
    try {
        const target = new URL(options.url, location.href);
        if (target.origin !== location.origin) throw new Error('Asset origin differs from page');
        const response = await fetch(target.href, {
            credentials: 'same-origin', cache: options.cacheMode, signal: controller.signal
        });
        if (response.status !== 200) throw new Error('Asset HTTP ' + response.status);
        if (new URL(response.url).origin !== location.origin) throw new Error('Asset redirected to another origin');
        const advertised = Number(response.headers.get('content-length') || 0);
        if (advertised > options.maxBytes) throw new Error('Asset exceeds size limit');
        const contentType = response.headers.get('content-type') || '';
        let blob;
        if (response.body && response.body.getReader) {
            const reader = response.body.getReader();
            const chunks = [];
            let size = 0;
            while (true) {
                const next = await reader.read();
                if (next.done) break;
                size += next.value.byteLength;
                if (size > options.maxBytes) {
                    await reader.cancel();
                    throw new Error('Asset exceeds size limit');
                }
                chunks.push(next.value);
            }
            blob = new Blob(chunks, {type: contentType});
        } else {
            blob = await response.blob();
        }
        if (!blob.size || blob.size > options.maxBytes) throw new Error('Invalid asset size');
        const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error('Cannot read asset bytes'));
            reader.readAsDataURL(blob);
        });
        return JSON.stringify({ok: true, status: response.status,
            contentType, size: blob.size, finalUrl: response.url,
            base64: dataUrl.slice(dataUrl.indexOf(',') + 1)});
    } catch (error) {
        controller.abort();
        return JSON.stringify({ok: false, error: String(error.message || error)});
    } finally {
        clearTimeout(timer);
    }
})(__OPTIONS__)
"""


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme.lower(), parts.hostname.lower(), parts.port or (443 if parts.scheme == "https" else 80)


def fetch_browser_binary(engine, url, *, page_url, timeout_seconds=45, max_bytes=10_000_000,
                         cache_mode="no-store"):
    for value, name in ((timeout_seconds, "timeout_seconds"), (max_bytes, "max_bytes")):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer.")
    if cache_mode not in ("no-store", "force-cache"):
        raise ValueError("cache_mode must be no-store or force-cache.")
    url = http_url(url, page_url)
    page_url = http_url(page_url, page_url)
    if _origin(url) != _origin(page_url):
        raise ValueError("Browser asset download requires the page's same origin.")
    expression = BINARY_FETCH_EXPRESSION.replace("__OPTIONS__", json.dumps({
        "url": url, "timeoutMs": timeout_seconds * 1000, "maxBytes": max_bytes,
        "cacheMode": cache_mode,
    }))
    use_native = (
        getattr(engine, "navigationMode", None) == "uc_open"
        or getattr(engine.sb, "cdp", None) is None
    )
    if use_native:
        driver = engine.sb.driver
        previous = driver.timeouts.script
        driver.set_script_timeout(timeout_seconds + 10)
        try:
            result = driver.execute_async_script(
                "const done = arguments[arguments.length - 1];\n"
                + expression + ".then(done).catch(e => done(JSON.stringify({ok:false,error:String(e)})));"
            )
        finally:
            driver.set_script_timeout(previous)
    else:
        result = engine.evaluateAwait(expression)
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError((result.get("error") if isinstance(result, dict) else None)
                           or "Browser returned no usable asset response.")
    if result.get("status") != 200:
        raise ValueError("Asset response was not HTTP 200.")
    payload = base64.b64decode(result["base64"], validate=True)
    if not payload or len(payload) > max_bytes or len(payload) != result.get("size"):
        raise ValueError("Downloaded asset byte count is invalid.")
    final_url = http_url(result.get("finalUrl"), url)
    if _origin(final_url) != _origin(page_url):
        raise ValueError("Asset response changed origin.")
    return payload, {"url": url, "final_url": final_url,
                     "content_type": result.get("contentType", ""), "size_bytes": len(payload)}
