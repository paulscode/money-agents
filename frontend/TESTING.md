# Money Agents - Frontend Testing

Comprehensive test suite for the Money Agents frontend using Vitest and React Testing Library.

## Setup

Tests are configured with:
- **Vitest** - Fast unit test framework
- **React Testing Library** - React component testing
- **@testing-library/user-event** - User interaction simulation
- **jsdom** - DOM environment for tests

## Running Tests

```bash
# Run all tests
npm test

# Run tests in watch mode
npm test -- --watch

# Run tests with UI
npm run test:ui

# Generate coverage report
npm run test:coverage
```

## Test Structure

```
src/
├── test/
│   ├── setup.ts          # Global test setup
│   ├── test-utils.tsx    # Custom render utilities
│   └── mocks.ts          # Mock data and API responses
├── components/
│   └── proposals/
│       ├── ProposalCard.tsx
│       └── ProposalCard.test.tsx
└── pages/
    ├── ProposalsPage.tsx
    ├── ProposalsPage.test.tsx
    ├── ProposalDetailPage.tsx
    ├── ProposalDetailPage.test.tsx
    ├── ProposalCreatePage.tsx
    └── ProposalCreatePage.test.tsx
```

## Test Coverage

### ProposalCard Component (10 tests)
- ✅ Renders title, summary, and all metadata
- ✅ Displays budget, risk, and returns correctly
- ✅ Status badge with correct colors
- ✅ Links to detail page
- ✅ Handles missing optional fields

### ProposalsPage (8 tests)
- ✅ Loading and empty states
- ✅ Displays proposals in grid/list view
- ✅ View toggle functionality
- ✅ Status filtering
- ✅ Filter dropdown options
- ✅ Filtered empty states

### ProposalDetailPage (12 tests)
- ✅ Loading and not found states
- ✅ Displays all proposal sections
- ✅ Key metrics cards
- ✅ Status badge and review actions
- ✅ Status update functionality
- ✅ Delete confirmation modal
- ✅ Navigation (back button)

### ProposalCreatePage (11 tests)
- ✅ Renders all form sections
- ✅ Required field validation
- ✅ Pre-filled JSON examples
- ✅ Form submission with valid data
- ✅ Invalid JSON error handling
- ✅ Risk level selection
- ✅ Loading state during submission
- ✅ Optional fields handling

## Writing New Tests

Use the custom `renderWithProviders` utility to render components with necessary context:

```typescript
import { renderWithProviders, userEvent, screen, waitFor } from '@/test/test-utils';
import { MyComponent } from './MyComponent';

describe('MyComponent', () => {
  it('does something', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MyComponent />);
    
    // Test interactions
    const button = screen.getByRole('button');
    await user.click(button);
    
    await waitFor(() => {
      expect(screen.getByText('Success')).toBeInTheDocument();
    });
  });
});
```

## Mock Data

Mock data is centralized in `src/test/mocks.ts`:
- `mockUser` - Test user
- `mockProposal` - Single proposal
- `mockProposals` - Array of proposals
- `mockApiResponses` - API response mocks

## Best Practices

1. **Test user behavior, not implementation** - Focus on what users see and do
2. **Use semantic queries** - Prefer `getByRole`, `getByLabelText` over `getByTestId`
3. **Async operations** - Always use `waitFor` for async updates
4. **Mock external dependencies** - Mock services, not internal utilities
5. **Descriptive test names** - Test names should clearly state what they verify

## CI/CD Integration

Tests run automatically on:
- Pull requests
- Pre-commit hooks (recommended)
- CI/CD pipeline

## Troubleshooting

### Tests timing out
Increase timeout in specific tests:
```typescript
it('slow test', async () => {
  // ...
}, 10000); // 10 second timeout
```

### Mock not working
Ensure mocks are cleared between tests:
```typescript
beforeEach(() => {
  vi.clearAllMocks();
});
```

### Component not rendering
Check that all providers are included in `renderWithProviders`

## Future Test Coverage

- [ ] Integration tests for full user flows
- [ ] E2E tests with Playwright
- [ ] Visual regression tests
- [ ] Performance tests
- [ ] Accessibility tests (a11y)
