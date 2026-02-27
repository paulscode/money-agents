/**
 * Unit tests for UtilizationChart component
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { UtilizationChart } from '../UtilizationChart';

describe('UtilizationChart', () => {
  const defaultProps = {
    avgDuration: 180,
    minDuration: 120,
    maxDuration: 240,
    scheduleInterval: 3600,
    utilization: 5, // 5% = 180/3600
  };

  describe('Rendering', () => {
    it('renders with valid data', () => {
      render(<UtilizationChart {...defaultProps} />);

      expect(screen.getByText('Schedule Utilization')).toBeInTheDocument();
    });

    it('shows utilization percentage', () => {
      render(<UtilizationChart {...defaultProps} />);

      expect(screen.getByText('5.0%')).toBeInTheDocument();
    });

    it('shows min/avg/max labels', () => {
      render(<UtilizationChart {...defaultProps} />);

      expect(screen.getByText('Min')).toBeInTheDocument();
      expect(screen.getByText('Avg')).toBeInTheDocument();
      expect(screen.getByText('Max')).toBeInTheDocument();
    });

    it('shows schedule interval in scale markers', () => {
      render(
        <UtilizationChart
          avgDuration={180}
          minDuration={120}
          maxDuration={240}
          scheduleInterval={3600}
          utilization={5}
        />
      );

      // 3600 seconds = 1 hour should be in scale markers
      expect(screen.getByText('1h')).toBeInTheDocument();
    });
  });

  describe('Utilization Display', () => {
    it('shows low utilization correctly', () => {
      render(<UtilizationChart {...defaultProps} utilization={5} />);

      expect(screen.getByText('5.0%')).toBeInTheDocument();
    });

    it('shows high utilization correctly', () => {
      render(<UtilizationChart {...defaultProps} utilization={80} />);

      expect(screen.getByText('80.0%')).toBeInTheDocument();
    });

    it('shows N/A for null utilization', () => {
      render(<UtilizationChart {...defaultProps} utilization={null} />);

      expect(screen.getByText('N/A')).toBeInTheDocument();
    });
  });

  describe('Warning Messages', () => {
    it('shows warning for high utilization (≥80%)', () => {
      render(
        <UtilizationChart
          avgDuration={3240}
          minDuration={3000}
          maxDuration={3400}
          scheduleInterval={3600}
          utilization={90}
        />
      );

      expect(screen.getByText(/High utilization/i)).toBeInTheDocument();
      expect(screen.getByText(/runs may overlap or timeout/i)).toBeInTheDocument();
    });

    it('shows info for low utilization (<20%)', () => {
      render(
        <UtilizationChart
          avgDuration={180}
          minDuration={120}
          maxDuration={240}
          scheduleInterval={3600}
          utilization={5}
        />
      );

      expect(screen.getByText(/Low utilization/i)).toBeInTheDocument();
      expect(screen.getByText(/decrease the schedule interval/i)).toBeInTheDocument();
    });

    it('shows no warning for medium utilization', () => {
      render(
        <UtilizationChart
          avgDuration={1800}
          minDuration={1500}
          maxDuration={2100}
          scheduleInterval={3600}
          utilization={50}
        />
      );

      expect(screen.queryByText(/High utilization/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Low utilization/i)).not.toBeInTheDocument();
    });

    it('shows no warning when utilization is exactly 20%', () => {
      render(<UtilizationChart {...defaultProps} utilization={20} />);

      expect(screen.queryByText(/Low utilization/i)).not.toBeInTheDocument();
    });

    it('shows warning when utilization is exactly 80%', () => {
      render(<UtilizationChart {...defaultProps} utilization={80} />);

      expect(screen.getByText(/High utilization/i)).toBeInTheDocument();
    });
  });

  describe('Null Values', () => {
    it('handles null avgDuration gracefully', () => {
      render(
        <UtilizationChart
          avgDuration={null}
          minDuration={null}
          maxDuration={null}
          scheduleInterval={3600}
          utilization={null}
        />
      );

      // Should show "no data" message
      expect(screen.getByText(/No run data available yet/i)).toBeInTheDocument();
    });

    it('handles null minDuration', () => {
      const { container } = render(
        <UtilizationChart
          avgDuration={180}
          minDuration={null}
          maxDuration={240}
          scheduleInterval={3600}
          utilization={5}
        />
      );

      // The component shows "-" for null min duration
      expect(container.textContent).toContain('-');
    });

    it('handles null maxDuration', () => {
      const { container } = render(
        <UtilizationChart
          avgDuration={180}
          minDuration={120}
          maxDuration={null}
          scheduleInterval={3600}
          utilization={5}
        />
      );

      // The component shows "-" for null max duration
      expect(container.textContent).toContain('-');
    });
  });

  describe('Visual Elements', () => {
    it('renders utilization bar', () => {
      const { container } = render(
        <UtilizationChart
          avgDuration={1800}
          minDuration={1500}
          maxDuration={2100}
          scheduleInterval={3600}
          utilization={50}
        />
      );

      // Should have a progress-like bar element with background color
      const progressBar = container.querySelector('[class*="bg-"][class*="rounded"]');
      expect(progressBar).toBeInTheDocument();
    });

    it('renders scale markers', () => {
      render(
        <UtilizationChart
          avgDuration={1800}
          minDuration={1500}
          maxDuration={2100}
          scheduleInterval={3600}
          utilization={50}
        />
      );

      // Should have 0 at start
      expect(screen.getByText('0')).toBeInTheDocument();
    });
  });

  describe('Color Coding', () => {
    it('uses green color for low utilization', () => {
      const { container } = render(
        <UtilizationChart {...defaultProps} utilization={20} />
      );

      // Should have green-themed elements
      const greenElement = container.querySelector('[class*="green"]');
      expect(greenElement).toBeInTheDocument();
    });

    it('uses yellow color for medium utilization', () => {
      const { container } = render(
        <UtilizationChart {...defaultProps} utilization={55} />
      );

      // Should have yellow-themed elements
      const yellowElement = container.querySelector('[class*="yellow"]');
      expect(yellowElement).toBeInTheDocument();
    });

    it('uses red color for high utilization', () => {
      const { container } = render(
        <UtilizationChart {...defaultProps} utilization={85} />
      );

      // Should have red-themed elements
      const redElement = container.querySelector('[class*="red"]');
      expect(redElement).toBeInTheDocument();
    });
  });

  describe('Edge Cases', () => {
    it('handles very small values', () => {
      render(
        <UtilizationChart
          avgDuration={1}
          minDuration={1}
          maxDuration={1}
          scheduleInterval={60}
          utilization={1.67}
        />
      );

      expect(screen.getByText('1.7%')).toBeInTheDocument();
    });

    it('handles very large values', () => {
      render(
        <UtilizationChart
          avgDuration={43200}
          minDuration={36000}
          maxDuration={50400}
          scheduleInterval={86400}
          utilization={50}
        />
      );

      expect(screen.getByText('50.0%')).toBeInTheDocument();
    });

    it('handles utilization over 100%', () => {
      render(
        <UtilizationChart
          avgDuration={4320}
          minDuration={3600}
          maxDuration={5400}
          scheduleInterval={3600}
          utilization={120}
        />
      );

      expect(screen.getByText('120.0%')).toBeInTheDocument();
      expect(screen.getByText(/High utilization/i)).toBeInTheDocument();
    });
  });
});
