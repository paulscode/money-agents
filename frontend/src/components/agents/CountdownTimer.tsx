/**
 * CountdownTimer Component
 * 
 * Displays a countdown to a target time, updating every second.
 * Re-syncs with server data when the targetTime prop changes.
 * Stops at zero (doesn't go negative).
 */
import { useState, useEffect, useRef } from 'react';
import { formatDuration } from '@/services/agentService';

interface CountdownTimerProps {
  /** ISO timestamp of the target time */
  targetTime: string | null;
  /** Label to show before the time (e.g., "Next: ") */
  label?: string;
  /** Text to show when countdown reaches zero */
  zeroText?: string;
  /** CSS classes for the time value */
  className?: string;
  /** CSS classes for the label */
  labelClassName?: string;
}

export function CountdownTimer({ 
  targetTime, 
  label = '',
  zeroText = 'Now',
  className = 'text-white font-medium',
  labelClassName = 'text-gray-400',
}: CountdownTimerProps) {
  const [secondsRemaining, setSecondsRemaining] = useState<number | null>(null);
  const intervalRef = useRef<number | null>(null);

  // Calculate initial seconds and sync when targetTime changes
  useEffect(() => {
    if (!targetTime) {
      setSecondsRemaining(null);
      return;
    }

    const calculateRemaining = () => {
      const remaining = Math.max(0, new Date(targetTime).getTime() - Date.now());
      return Math.floor(remaining / 1000);
    };

    // Set initial value
    setSecondsRemaining(calculateRemaining());

    // Start countdown interval
    intervalRef.current = window.setInterval(() => {
      setSecondsRemaining(prev => {
        if (prev === null || prev <= 0) {
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [targetTime]);

  // Stop interval when we hit zero
  useEffect(() => {
    if (secondsRemaining === 0 && intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, [secondsRemaining]);

  if (secondsRemaining === null) {
    return null;
  }

  return (
    <>
      {label && <span className={labelClassName}>{label}</span>}
      <span className={className}>
        {secondsRemaining === 0 ? zeroText : formatDuration(secondsRemaining)}
      </span>
    </>
  );
}
