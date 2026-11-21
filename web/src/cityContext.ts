import { createContext, useContext } from 'react';
import { CITIES, type CityData } from './data';

/** The city the home page is showing; every section reads its numbers through useCity(). */
export const CityCtx = createContext<{ city: CityData; setCity: (name: string) => void }>({ city: CITIES[0], setCity: () => {} });
export const useCity = () => useContext(CityCtx);
