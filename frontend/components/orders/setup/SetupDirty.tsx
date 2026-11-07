"use client";

import { createContext, useContext } from "react";

// The setup header asks before Close when the settings form has unsaved edits.
export const SetupDirtyContext = createContext<(dirty: boolean) => void>(() => undefined);

export const useSetupDirty = () => useContext(SetupDirtyContext);
