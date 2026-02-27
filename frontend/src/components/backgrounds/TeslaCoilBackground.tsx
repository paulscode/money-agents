/**
 * TeslaCoilBackground Component
 * 
 * Global background with Tesla coils and electric arcs.
 * Used across all pages via the Layout component.
 */

export function TeslaCoilBackground() {
  return (
    <div className="fixed inset-0 overflow-hidden pointer-events-none z-0">
      {/* Left Tesla Coil */}
      <svg 
        className="absolute -left-8 top-1/2 -translate-y-1/2 w-32 h-64 opacity-[0.07]"
        viewBox="0 0 100 200" 
        fill="none"
      >
        {/* Coil base */}
        <rect x="35" y="160" width="30" height="35" rx="3" fill="currentColor" className="text-neon-cyan" />
        <rect x="40" y="150" width="20" height="15" rx="2" fill="currentColor" className="text-neon-cyan" />
        
        {/* Coil windings */}
        {[...Array(12)].map((_, i) => (
          <ellipse 
            key={i}
            cx="50" 
            cy={145 - i * 8} 
            rx={14 - i * 0.5} 
            ry="4" 
            stroke="currentColor" 
            strokeWidth="2"
            fill="none"
            className="text-neon-cyan"
          />
        ))}
        
        {/* Top electrode */}
        <circle cx="50" cy="45" r="12" fill="currentColor" className="text-neon-cyan" />
        <circle cx="50" cy="45" r="8" fill="currentColor" className="text-neon-purple opacity-50" />
        
        {/* Electric arcs - animated */}
        <g className="animate-pulse">
          <path 
            d="M62 45 Q75 30, 85 50 Q78 55, 90 70" 
            stroke="currentColor" 
            strokeWidth="1.5" 
            fill="none"
            className="text-neon-cyan"
            strokeLinecap="round"
          />
          <path 
            d="M58 38 Q70 20, 65 10 Q72 5, 80 15" 
            stroke="currentColor" 
            strokeWidth="1" 
            fill="none"
            className="text-neon-purple"
            strokeLinecap="round"
          />
        </g>
        <g className="animate-pulse" style={{ animationDelay: '0.5s' }}>
          <path 
            d="M38 45 Q25 35, 15 55 Q22 60, 10 75" 
            stroke="currentColor" 
            strokeWidth="1.5" 
            fill="none"
            className="text-neon-cyan"
            strokeLinecap="round"
          />
        </g>
      </svg>
      
      {/* Right Tesla Coil */}
      <svg 
        className="absolute -right-8 top-1/2 -translate-y-1/2 w-32 h-64 opacity-[0.07] scale-x-[-1]"
        viewBox="0 0 100 200" 
        fill="none"
      >
        {/* Coil base */}
        <rect x="35" y="160" width="30" height="35" rx="3" fill="currentColor" className="text-neon-purple" />
        <rect x="40" y="150" width="20" height="15" rx="2" fill="currentColor" className="text-neon-purple" />
        
        {/* Coil windings */}
        {[...Array(12)].map((_, i) => (
          <ellipse 
            key={i}
            cx="50" 
            cy={145 - i * 8} 
            rx={14 - i * 0.5} 
            ry="4" 
            stroke="currentColor" 
            strokeWidth="2"
            fill="none"
            className="text-neon-purple"
          />
        ))}
        
        {/* Top electrode */}
        <circle cx="50" cy="45" r="12" fill="currentColor" className="text-neon-purple" />
        <circle cx="50" cy="45" r="8" fill="currentColor" className="text-neon-cyan opacity-50" />
        
        {/* Electric arcs - animated */}
        <g className="animate-pulse" style={{ animationDelay: '0.3s' }}>
          <path 
            d="M62 45 Q75 30, 85 50 Q78 55, 90 70" 
            stroke="currentColor" 
            strokeWidth="1.5" 
            fill="none"
            className="text-neon-purple"
            strokeLinecap="round"
          />
          <path 
            d="M55 35 Q60 15, 75 20" 
            stroke="currentColor" 
            strokeWidth="1" 
            fill="none"
            className="text-neon-cyan"
            strokeLinecap="round"
          />
        </g>
        <g className="animate-pulse" style={{ animationDelay: '0.7s' }}>
          <path 
            d="M38 45 Q25 35, 15 55 Q22 60, 10 75" 
            stroke="currentColor" 
            strokeWidth="1.5" 
            fill="none"
            className="text-neon-purple"
            strokeLinecap="round"
          />
        </g>
      </svg>
      
      {/* Center electric arc connecting the coils - very subtle */}
      <svg 
        className="absolute inset-0 w-full h-full opacity-[0.04]"
        viewBox="0 0 400 200"
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id="arcGradient" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#00d9ff" />
            <stop offset="50%" stopColor="#9d4edd" />
            <stop offset="100%" stopColor="#00d9ff" />
          </linearGradient>
        </defs>
        {/* Main arc */}
        <path 
          d="M30 100 Q100 60, 150 100 Q180 130, 200 90 Q220 50, 250 100 Q300 140, 370 100" 
          stroke="url(#arcGradient)" 
          strokeWidth="2" 
          fill="none"
          strokeLinecap="round"
          className="animate-pulse"
        />
        {/* Secondary arcs */}
        <path 
          d="M50 110 Q120 150, 200 100 Q280 50, 350 110" 
          stroke="url(#arcGradient)" 
          strokeWidth="1" 
          fill="none"
          strokeLinecap="round"
          className="animate-pulse"
          style={{ animationDelay: '0.4s' }}
        />
      </svg>
      
      {/* Subtle glow spots */}
      <div className="absolute left-12 top-1/2 -translate-y-1/2 w-20 h-20 bg-neon-cyan/10 rounded-full blur-xl animate-pulse" />
      <div className="absolute right-12 top-1/2 -translate-y-1/2 w-20 h-20 bg-neon-purple/10 rounded-full blur-xl animate-pulse" style={{ animationDelay: '0.5s' }} />
    </div>
  );
}

export default TeslaCoilBackground;
