'use strict';

function resolveBindHost(useHttp, requestedHost = '') {
    const override = typeof requestedHost === 'string' ? requestedHost.trim() : '';
    if (override) return override;
    return '0.0.0.0';
}

module.exports = { resolveBindHost };
