import React, { useState, useEffect, useRef } from 'react';

export default function RangePopover({ label, min, max, valueMin, valueMax, step, onChangeMin, onChangeMax, formatVal }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const isAll = valueMin <= min && valueMax >= max;
  const btnLabel = isAll ? `${label}: All` : `${label}: ${formatVal ? formatVal(valueMin) : valueMin} - ${formatVal ? formatVal(valueMax) : valueMax}`;

  return (
    <div className="range-popover-wrap" ref={ref}>
      <button
        type="button"
        className={`btn btn-secondary btn-sm range-trigger${open ? ' active' : ''}`}
        onClick={() => setOpen(!open)}
      >
        {btnLabel}
      </button>
      {open && (
        <div className="range-popover" onClick={(e) => e.stopPropagation()}>
          <div className="range-popover-header">
            <span>{label} range</span>
            <strong>{formatVal ? formatVal(valueMin) : valueMin} - {formatVal ? formatVal(valueMax) : valueMax}</strong>
          </div>
          <div className="range-popover-row">
            <span className="range-popover-label">From</span>
            <input type="range" min={min} max={max} step={step} value={valueMin}
              onChange={(e) => onChangeMin(Number(e.target.value))} />
            <span className="range-popover-value">{formatVal ? formatVal(valueMin) : valueMin}</span>
          </div>
          <div className="range-popover-row">
            <span className="range-popover-label">To</span>
            <input type="range" min={min} max={max} step={step} value={valueMax}
              onChange={(e) => onChangeMax(Number(e.target.value))} />
            <span className="range-popover-value">{formatVal ? formatVal(valueMax) : valueMax}</span>
          </div>
        </div>
      )}
    </div>
  );
}
