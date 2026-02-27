/**
 * InvoiceQR — Renders a BOLT11 Lightning invoice as a scannable QR code
 * with a copy button and optional truncated text display.
 */
import { useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Copy, Check, QrCode } from 'lucide-react';

interface InvoiceQRProps {
  /** BOLT11 payment request string (e.g. lnbc…) */
  paymentRequest: string;
  /** QR code pixel size (default 200) */
  size?: number;
  /** Show the truncated invoice text below the QR */
  showText?: boolean;
  /** Optional label above the QR */
  label?: string;
}

export function InvoiceQR({
  paymentRequest,
  size = 200,
  showText = true,
  label,
}: InvoiceQRProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(paymentRequest);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // BOLT11 invoices should be uppercased in the QR for better scannability
  // (uppercase alphanumeric mode is more compact in QR encoding)
  const qrValue = paymentRequest.toUpperCase();

  return (
    <div className="flex flex-col items-center gap-3 p-4 bg-white rounded-xl">
      {label && (
        <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
          {label}
        </span>
      )}
      <QRCodeSVG
        value={qrValue}
        size={size}
        level="M"
        includeMargin={false}
      />
      {showText && (
        <div className="flex items-center gap-2 max-w-full">
          <code className="text-[10px] text-gray-600 truncate max-w-[180px]">
            {paymentRequest}
          </code>
          <button
            onClick={handleCopy}
            className="flex-shrink-0 p-1 rounded hover:bg-gray-100 transition-colors"
            title="Copy invoice"
          >
            {copied
              ? <Check className="w-3.5 h-3.5 text-green-600" />
              : <Copy className="w-3.5 h-3.5 text-gray-400" />}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Compact inline QR toggle — shows a small QR icon that expands the full QR code.
 */
export function InvoiceQRToggle({ paymentRequest }: { paymentRequest: string }) {
  const [show, setShow] = useState(false);

  return (
    <div>
      <button
        onClick={() => setShow(!show)}
        className="inline-flex items-center gap-1 text-xs text-neon-cyan hover:text-neon-cyan/80 transition-colors"
        title={show ? 'Hide QR code' : 'Show QR code'}
      >
        <QrCode className="w-3.5 h-3.5" />
        {show ? 'Hide QR' : 'Show QR'}
      </button>
      {show && (
        <div className="mt-2">
          <InvoiceQR paymentRequest={paymentRequest} size={180} label="Lightning Invoice" />
        </div>
      )}
    </div>
  );
}
