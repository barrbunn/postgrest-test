// njs handler: mimics the Apigee policies on the gateway.
//
// 1. VerifyJWT: verify the client's user JWT (RS256) against the IdP public
//    key, then check exp/iss/aud.
// 2. Claim extraction: role = realm_access.roles[0].
// 3. GenerateJWT: sign a service JWT (RS256, gateway private key) that
//    embeds the original user JWT.
// 4. Forward via an internal subrequest carrying the service JWT and
//    X-User-Role headers (the subrequest location does mTLS proxy_pass).

import fs from 'fs';

// Note: the njs global `crypto` object (WebCrypto) is used directly; the
// node-style 'crypto' module must NOT be imported, it shadows the global.
const subtle = globalThis.crypto.subtle;

const APIGEE_CONFIG = JSON.parse(fs.readFileSync('/certs/apigee-config.json', 'utf8'));
const EXPECTED_ISS = APIGEE_CONFIG.issuer;
const EXPECTED_AUD = APIGEE_CONFIG.audience;
const SERVICE_TTL = 60;

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

const IDP_PUBLIC_KEY = pemToDer(fs.readFileSync('/certs/idp-public.pem', 'utf8'));
const GATEWAY_PRIVATE_KEY = pemToDer(fs.readFileSync('/certs/gateway-jwt.key', 'utf8'));

function b64Decode(s) {
    const bytes = [];
    for (let i = 0; i < s.length; i += 4) {
        const n = B64.indexOf(s[i]) * 262144
            + B64.indexOf(s[i + 1]) * 4096
            + (s[i + 2] === '=' ? 0 : B64.indexOf(s[i + 2])) * 64
            + (s[i + 3] === '=' ? 0 : B64.indexOf(s[i + 3]));
        bytes.push((n >> 16) & 255);
        if (s[i + 2] !== '=') {
            bytes.push((n >> 8) & 255);
        }
        if (s[i + 3] !== '=') {
            bytes.push(n & 255);
        }
    }
    return new Uint8Array(bytes);
}

function pemToDer(pem) {
    const lines = pem.split('\n').filter(function (l) { return !l.startsWith('-----'); });
    return b64Decode(lines.join(''));
}

function b64url(bytes) {
    let out = '';
    for (let i = 0; i < bytes.length; i += 3) {
        const a = bytes[i];
        const b = i + 1 < bytes.length ? bytes[i + 1] : 0;
        const c = i + 2 < bytes.length ? bytes[i + 2] : 0;
        out += B64[a >> 2] + B64[((a & 3) << 4) | (b >> 4)];
        out += i + 1 < bytes.length ? B64[((b & 15) << 2) | (c >> 6)] : '=';
        out += i + 2 < bytes.length ? B64[c & 63] : '=';
    }
    return out.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64urlDecode(str) {
    const s = str.replace(/-/g, '+').replace(/_/g, '/');
    const pad = s.length % 4 === 0 ? '' : '='.repeat(4 - (s.length % 4));
    return b64Decode(s + pad);
}

function parseJwt(token) {
    const parts = token.split('.');
    if (parts.length !== 3) {
        return null;
    }
    const bytes = b64urlDecode(parts[1]);
    let json = '';
    for (let i = 0; i < bytes.length; i++) {
        json += String.fromCharCode(bytes[i]);
    }
    try {
        return JSON.parse(json);
    } catch (e) {
        return null;
    }
}

async function verify(token) {
    const parts = token.split('.');
    if (parts.length !== 3) {
        return null;
    }
    try {
        const key = await subtle.importKey('spki', IDP_PUBLIC_KEY,
            { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
        const data = new TextEncoder().encode(parts[0] + '.' + parts[1]);
        const sig = new Uint8Array(b64urlDecode(parts[2]));
        const ok = await subtle.verify({ name: 'RSASSA-PKCS1-v1_5' }, key, sig, data);
        return ok ? parseJwt(token) : null;
    } catch (e) {
        return null;
    }
}

async function signServiceJwt(claims) {
    const key = await subtle.importKey('pkcs8', GATEWAY_PRIVATE_KEY,
        { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['sign']);
    const header = b64url(new TextEncoder().encode(JSON.stringify({ alg: 'RS256', typ: 'JWT' })));
    const payload = b64url(new TextEncoder().encode(JSON.stringify(claims)));
    const data = new TextEncoder().encode(header + '.' + payload);
    const sig = await subtle.sign({ name: 'RSASSA-PKCS1-v1_5' }, key, data);
    return header + '.' + payload + '.' + b64url(new Uint8Array(sig));
}

function reject(r, status, message) {
    r.status = status;
    r.headersOut['Content-Type'] = 'application/json';
    r.return(status, JSON.stringify({ error: message }));
}

async function handle(r) {
    const auth = r.headersIn['Authorization'];
    if (!auth || !auth.startsWith('Bearer ')) {
        reject(r, 401, 'missing user JWT');
        return;
    }
    const token = auth.slice(7).trim();

    const claims = await verify(token);
    if (!claims) {
        reject(r, 401, 'invalid user JWT');
        return;
    }
    const now = Math.floor(Date.now() / 1000);
    if (!claims.exp || claims.exp < now) {
        reject(r, 401, 'user JWT expired');
        return;
    }
    if (claims.iss !== EXPECTED_ISS) {
        reject(r, 401, 'unexpected issuer');
        return;
    }
    if (claims.aud !== EXPECTED_AUD) {
        reject(r, 401, 'unexpected audience');
        return;
    }

    let role = 'anon';
    const realmRoles = claims.realm_access && claims.realm_access.roles;
    if (realmRoles && realmRoles.length > 0) {
        role = realmRoles[0];
    }

    const serviceJwt = await signServiceJwt({
        iss: 'apigee',
        sub: claims.sub || '',
        role: role,
        user_jwt: token,
        exp: now + SERVICE_TTL,
    });

    r.variables.service_jwt = serviceJwt;
    r.variables.user_role = role;

    const args = r.variables.args ? '?' + r.variables.args : '';
    r.subrequest('/_backend' + r.uri + args, function (res) {
        r.status = res.status;
        r.headersOut['Content-Type'] = res.headersOut['Content-Type'] || 'application/json';
        r.return(res.status, res.responseBuffer ? res.responseBuffer : '');
    });
}

export default { handle };
