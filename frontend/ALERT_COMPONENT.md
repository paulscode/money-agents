# Custom Alert Component

## Overview
The Alert component replaces browser `alert()` dialogs with a styled notification system that matches the Money Agents dark theme with neon accents.

## Features

### ✨ Visual Design
- **Dark theme integration** - Matches the app's aesthetic
- **Type-specific styling** - Different colors for success, error, warning, info
- **Smooth animations** - Slides in from top with fade effect
- **Close button** - Manual dismissal option
- **Auto-close** - Optional automatic dismissal after specified duration

### 🎯 Improved UX for Form Validation

#### Before
```
❌ Browser alert: "Invalid JSON in one or more fields. Please check your input."
```
- Generic message
- No field identification
- Browser-styled (doesn't match app theme)
- Interrupts user flow

#### After
```
✅ Custom Alert: 
   Title: "Invalid JSON"
   Message: "The 'Stop Loss Threshold' field contains invalid JSON. 
            Please check the syntax and try again."
```
- Specific field name identified
- Helpful error message
- Matches app styling
- Non-blocking (appears at top)
- Can be dismissed

## Usage

### Basic Usage
```tsx
import { Alert } from '@/components/common/Alert';
import { useState } from 'react';

function MyComponent() {
  const [error, setError] = useState<string | null>(null);
  
  return (
    <>
      {error && (
        <Alert
          type="error"
          title="Error"
          message={error}
          onClose={() => setError(null)}
        />
      )}
      {/* Your component content */}
    </>
  );
}
```

### With Auto-Close
```tsx
<Alert
  type="success"
  title="Success!"
  message="Proposal created successfully"
  onClose={() => setSuccess(null)}
  autoClose={true}
  autoCloseDuration={3000}
/>
```

## Alert Types

| Type | Color | Icon | Use Case |
|------|-------|------|----------|
| `error` | Red | XCircle | Errors, validation failures |
| `warning` | Yellow | AlertCircle | Warnings, cautions |
| `success` | Green | CheckCircle | Success messages |
| `info` | Blue | Info | Information, tips |

## Implementation in ProposalCreatePage

### Enhanced Validation
The form now validates each JSON field individually and shows exactly which field has an error:

```tsx
// Before: Generic error
catch (error) {
  alert('Invalid JSON in one or more fields.');
}

// After: Specific field identification
for (const field of requiredFields) {
  try {
    proposalData[field] = JSON.parse(jsonFields[field]);
  } catch {
    setAlertError({
      title: 'Invalid JSON',
      message: `The "${jsonFieldLabels[field]}" field contains invalid JSON. 
                Please check the syntax and try again.`,
    });
    return;
  }
}
```

### Field Labels
Each JSON field has a user-friendly label:
- `stop_loss_threshold` → "Stop Loss Threshold"
- `success_criteria` → "Success Criteria"
- `required_tools` → "Required Tools"
- `required_inputs` → "Required Inputs"
- `recurring_costs` → "Recurring Costs"
- `expected_returns` → "Expected Returns"
- `implementation_timeline` → "Implementation Timeline"
- `tags` → "Tags"

## Testing

The Alert component is fully tested:
- ✅ Displays with correct styling based on type
- ✅ Shows title and message
- ✅ Can be closed manually
- ✅ Integrates with form validation
- ✅ Shows specific field names in errors
- ✅ All 45 tests passing

## Benefits

1. **Better User Experience**
   - Users know exactly which field has an error
   - No need to check each JSON field manually
   - Consistent with app's design language

2. **Accessibility**
   - Proper ARIA role (`role="alert"`)
   - Keyboard accessible close button
   - Clear visual hierarchy

3. **Reusability**
   - Can be used throughout the app
   - Supports multiple alert types
   - Flexible configuration options

4. **Developer Experience**
   - Easy to integrate
   - Type-safe with TypeScript
   - Consistent API
