// Copyright (c) Meta Platforms, Inc. and affiliates.

'use client';

/**
 * @file SegmentedControl.tsx
 * @input Uses React, StyleX, SegmentedControlContext, useListFocus, useKeyboardHint
 * @output Exports SegmentedControl component and SegmentedControlProps type
 * @position Container wrapper; provides context to SegmentedControlItem children
 *
 * SYNC: When modified, update:
 * - /packages/core/src/SegmentedControl/SegmentedControl.doc.mjs
 * - /packages/core/src/SegmentedControl/index.ts
 * - /packages/core/src/SegmentedControl/SegmentedControl.test.tsx
 * - /packages/cli/templates/blocks/components/SegmentedControl/ (showcase blocks)
 */

import React, {useMemo, useCallback, type ReactNode} from 'react';
import * as stylex from '@stylexjs/stylex';
import {colorVars, spacingVars, radiusVars} from '../theme/tokens.stylex';
import {SegmentedControlContext} from './SegmentedControlContext';
import {useListFocus} from '../hooks/useListFocus';
import {useKeyboardHint} from '../hooks/useKeyboardHint';
import type {
  SegmentedControlSize,
  SegmentedControlLayout,
} from './SegmentedControlContext';
import {mergeProps, mergeRefs} from '../utils';
import {useSize} from '../SizeContext/SizeContext';
import type {BaseProps} from '../BaseProps';
import {themeProps} from '../utils/themeProps';

export interface SegmentedControlProps extends Omit<
  BaseProps<HTMLDivElement>,
  'onChange'
> {
  ref?: React.Ref<HTMLDivElement>;
  /**
   * The currently selected value (controlled).
   */
  value: string;
  /**
   * Callback fired when a segment is selected.
   */
  onChange: (value: string) => void;
  /**
   * Accessible label for the radio group (used as aria-label, never rendered visually).
   */
  label: string;
  /**
   * Size variant for the control.
   * @default 'md'
   */
  size?: SegmentedControlSize;
  /**
   * Layout mode for segment sizing.
   * - `'hug'` (default): each segment hugs its content width.
   * - `'fill'`: segments stretch equally to fill the container width.
   * @default 'hug'
   */
  layout?: SegmentedControlLayout;
  /**
   * Whether the entire control is disabled.
   * @default false
   */
  isDisabled?: boolean;
  /**
   * SegmentedControlItem children.
   */
  children: ReactNode;
}

// =============================================================================
// Styles
// =============================================================================

const styles = stylex.create({
  container: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: spacingVars['--spacing-0-5'],
    '--_segmented-control-padding': spacingVars['--spacing-0-5'],
    padding: 'var(--_segmented-control-padding)',
    backgroundColor: colorVars['--color-neutral'],
  },
  fill: {
    display: 'flex',
    width: '100%',
  },
  disabled: {
    opacity: 0.5,
    pointerEvents: 'none',
  },
});

const sizeStyles = stylex.create({
  sm: {
    '--_segmented-control-radius': radiusVars['--radius-element'],
    borderRadius: 'var(--_segmented-control-radius)',
  },
  md: {
    '--_segmented-control-radius': radiusVars['--radius-element'],
    borderRadius: 'var(--_segmented-control-radius)',
  },
  lg: {
    '--_segmented-control-radius': radiusVars['--radius-element'],
    borderRadius: 'var(--_segmented-control-radius)',
  },
});

/**
 * Segmented button group for single selection (radio group semantics).
 * Visually resembles a tab bar but controls a value, not a view.
 *
 * @example
 * ```
 * <SegmentedControl value={view} onChange={setView} label="View mode">
 *   <SegmentedControlItem value="grid" label="Grid" />
 *   <SegmentedControlItem value="list" label="List" />
 *   <SegmentedControlItem value="table" label="Table" />
 * </SegmentedControl>
 * ```
 */
export function SegmentedControl({
  ref,
  value,
  onChange,
  label,
  size: sizeProp,
  layout = 'hug',
  isDisabled = false,
  children,
  xstyle,
  className,
  style,
}: SegmentedControlProps) {
  const size = useSize(sizeProp, 'md');

  // Roving tabindex + arrow/Home/End navigation is owned by the shared
  // useListFocus primitive: it stamps a single tab stop (tabIndex 0/-1) across
  // the radios, skips disabled ones, wraps at the ends, handles Home/End, and
  // repairs the tab stop on mount and whenever items mount/disable — replacing
  // the component's former inline keyboard handler and tab-stop repair effect.
  const {listRef, handleKeyDown, handleFocus} = useListFocus<HTMLDivElement>({
    itemSelector: '[role="radio"]:not([aria-disabled="true"])',
    hasRovingTabIndex: true,
    wrap: true,
    orientation: 'horizontal',
  });

  const hint = useKeyboardHint({
    orientation: 'horizontal',
    isEnabled: !isDisabled,
  });

  const handleContainerKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      hint.onKeyDown(e);
      handleKeyDown(e);
    },
    [hint, handleKeyDown],
  );

  // Selection-follows-focus (APG radiogroup): useListFocus only *moves* focus,
  // so whenever it lands focus on a new radio (arrow/Home/End, or a click) we
  // select that radio's value. Reading the focused element's data-value here
  // keeps selection in lockstep with focus without duplicating the navigation
  // logic. Disabled radios are ignored (they should never become the value),
  // and the already-selected value is skipped so an initial Tab-in (or a click
  // on the current segment) is a no-op, matching click behavior.
  const handleContainerFocus = useCallback(
    (e: React.FocusEvent) => {
      hint.onFocus(e);
      handleFocus(e);
      if (isDisabled) {
        return;
      }
      const focused = (e.target as HTMLElement | null)?.closest<HTMLElement>(
        '[role="radio"][data-value]',
      );
      if (!focused || focused.getAttribute('aria-disabled') === 'true') {
        return;
      }
      const nextValue = focused.dataset.value;
      if (nextValue != null && nextValue !== value) {
        onChange(nextValue);
      }
    },
    [hint, handleFocus, isDisabled, onChange, value],
  );

  const contextValue = useMemo(
    () => ({value, onChange, size, layout, isDisabled}),
    [value, onChange, size, layout, isDisabled],
  );

  return (
    <SegmentedControlContext value={contextValue}>
      <div
        ref={mergeRefs(ref, listRef)}
        role="radiogroup"
        aria-label={label}
        aria-disabled={isDisabled || undefined}
        onKeyDown={handleContainerKeyDown}
        onFocus={handleContainerFocus}
        onBlur={hint.onBlur}
        {...mergeProps(
          themeProps('segmented-control', {size}),
          stylex.props(
            styles.container,
            sizeStyles[size],
            layout === 'fill' && styles.fill,
            isDisabled && styles.disabled,
            xstyle,
          ),
          className,
          style,
        )}>
        {children}
        {hint.hintElement}
      </div>
    </SegmentedControlContext>
  );
}

SegmentedControl.displayName = 'SegmentedControl';
