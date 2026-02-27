import type { ReactNode } from 'react';
import { Header } from './Header';
import { TeslaCoilBackground } from '@/components/backgrounds/TeslaCoilBackground';

interface LayoutProps {
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  return (
    <div className="min-h-screen bg-navy-950 relative">
      {/* Global Tesla Coil Background */}
      <TeslaCoilBackground />
      
      <Header />
      <main className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {children}
      </main>
    </div>
  );
}
