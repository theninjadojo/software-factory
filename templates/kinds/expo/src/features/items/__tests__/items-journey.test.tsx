import { fireEvent, screen, waitFor } from '@testing-library/react-native';
import { renderRouter } from 'expo-router/testing-library';

import { api } from '@/lib/api/client';
import { createTestQueryClient, QueryWrapper } from '@/test-utils';

import { NewItemScreen } from '../screens/NewItemScreen';
import { ItemsScreen } from '../screens/ItemsScreen';

// The generated client is the only thing mocked: the hooks, query cache and screens are real.
jest.mock('@/lib/api/client', () => ({
  ...jest.requireActual('@/lib/api/client'),
  api: { GET: jest.fn(), POST: jest.fn() },
}));
const mockApi = api as unknown as { GET: jest.Mock; POST: jest.Mock };

const ok = <T,>(data: T, status = 200) => ({ data, response: new Response(null, { status }) });
const milk = { id: 1, name: 'Milk', created_at: '2026-10-07T09:00:00Z', processed_at: null };

function renderApp() {
  const client = createTestQueryClient();
  return renderRouter(
    {
      index: ItemsScreen,
      'items/new': NewItemScreen,
    },
    { initialUrl: '/', wrapper: ({ children }) => <QueryWrapper client={client}>{children}</QueryWrapper> },
  );
}

describe('items', () => {
  beforeEach(() => jest.clearAllMocks());

  it('lists the items from the API', async () => {
    mockApi.GET.mockResolvedValue(ok([milk]));
    await renderApp();

    expect(await screen.findByText('Milk')).toBeOnTheScreen();
    expect(mockApi.GET).toHaveBeenCalledWith('/items', { params: { query: { limit: 100 } } });
  });

  it('says so when the API fails', async () => {
    mockApi.GET.mockResolvedValue({ error: { detail: 'boom' }, response: new Response(null, { status: 500 }) });
    await renderApp();

    expect(await screen.findByText('Could not load items.')).toBeOnTheScreen();
  });

  it('creates an item and shows it in the list', async () => {
    mockApi.GET.mockResolvedValueOnce(ok([])).mockResolvedValue(ok([milk]));
    mockApi.POST.mockResolvedValue(ok(milk, 201));
    await renderApp();

    await fireEvent.press(await screen.findByRole('link', { name: 'Add item' }));
    await fireEvent.changeText(await screen.findByLabelText('Name'), 'Milk');
    await fireEvent.press(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockApi.POST).toHaveBeenCalledWith('/items', { body: { name: 'Milk' } }));
    expect(await screen.findByText('Milk')).toBeOnTheScreen();
  });
});
