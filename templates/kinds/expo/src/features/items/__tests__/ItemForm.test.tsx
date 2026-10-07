import { fireEvent, render, screen } from '@testing-library/react-native';

import { ItemForm } from '../components/ItemForm';

describe('ItemForm', () => {
  it('submits the trimmed name', async () => {
    const onSubmit = jest.fn();
    await render(<ItemForm onSubmit={onSubmit} />);

    await fireEvent.changeText(screen.getByLabelText('Name'), '  Milk  ');
    await fireEvent.press(screen.getByRole('button', { name: 'Save' }));

    expect(onSubmit).toHaveBeenCalledWith('Milk');
  });

  it('does not submit an empty name', async () => {
    const onSubmit = jest.fn();
    await render(<ItemForm onSubmit={onSubmit} />);

    await fireEvent.press(screen.getByRole('button', { name: 'Save' }));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('shows an error', async () => {
    await render(<ItemForm onSubmit={jest.fn()} error="Could not save the item." />);
    expect(screen.getByRole('alert')).toHaveTextContent('Could not save the item.');
  });
});
