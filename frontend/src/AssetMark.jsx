import React, { useState } from 'react';

// Logo do ativo servida pelo próprio portal (/ativos/logo/X.svg). Sem logo, volta às iniciais.
const CRYPTO = { BTC: '₿', ETH: 'Ξ' };

export function AssetMark({ position: p, large = false }) {
  const [failed, setFailed] = useState(false);
  const region = p.country === 'BR' ? 'br' : 'us';
  const size = large ? ' large' : '';
  if (p.market === 'CRIPTO') {
    return <div className={`asset-monogram crypto${size}`} aria-hidden="true">{CRYPTO[p.symbol] || p.symbol.slice(0, 2)}</div>;
  }
  if (failed) return <div className={`asset-monogram ${region}${size}`}>{p.symbol.slice(0, 2)}</div>;
  return <div className={`asset-monogram has-logo ${region}${size}`}>
    <img src={`/ativos/logo/${encodeURIComponent(p.symbol)}.svg`} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
  </div>;
}
