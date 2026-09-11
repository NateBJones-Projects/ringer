// Copyright (c) Meta Platforms, Inc. and affiliates.

/**
 * @file childrenAsContent.test.tsx
 * @input Uses vitest, @testing-library/react
 * @output Verifies Layout renders children as a shorthand for the content slot
 *   (so the natural `<Layout>…</Layout>` form never renders a blank shell), with
 *   an explicit `content` prop taking precedence.
 */

import {describe, it, expect} from 'vitest';
import {render, screen} from '@testing-library/react';
import {Layout} from '../Layout';
import {LayoutContent} from '../LayoutContent';

describe('Layout children-as-content', () => {
  it('renders nested children in the content slot', () => {
    render(
      <Layout>
        <LayoutContent>
          <span data-testid="body">Body</span>
        </LayoutContent>
      </Layout>,
    );
    expect(screen.getByTestId('body')).toBeInTheDocument();
  });

  it('renders bare children (no LayoutContent wrapper) too', () => {
    render(
      <Layout>
        <span data-testid="bare">Bare</span>
      </Layout>,
    );
    expect(screen.getByTestId('bare')).toBeInTheDocument();
  });

  it('lets an explicit content prop win over children', () => {
    render(
      <Layout content={<span data-testid="slot">Slot</span>}>
        <span data-testid="child">Child</span>
      </Layout>,
    );
    expect(screen.getByTestId('slot')).toBeInTheDocument();
    expect(screen.queryByTestId('child')).not.toBeInTheDocument();
  });

  it('still supports the canonical slot-only API', () => {
    render(
      <Layout
        content={
          <LayoutContent>
            <span data-testid="canon">Canonical</span>
          </LayoutContent>
        }
      />,
    );
    expect(screen.getByTestId('canon')).toBeInTheDocument();
  });
});
