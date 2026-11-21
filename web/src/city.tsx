import type { ReactNode } from 'react';
import type { CityData } from './data';
import { CityCtx } from './cityContext';

export function CityProvider({ value, children }: { value: { city: CityData; setCity: (name: string) => void }; children: ReactNode }) {
  return <CityCtx.Provider value={value}>{children}</CityCtx.Provider>;
}
