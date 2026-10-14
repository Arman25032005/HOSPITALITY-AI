"use client";

import { createContext, useContext } from "react";
import { Hotel, Me, Role } from "@/lib/types";

export interface StaffCtx {
  me: Me; hotels: Hotel[]; hotel: Hotel; setHotelId: (id: string) => void;
  /** Presentation only: the API enforces every permission independently. */
  can: (...roles: Role[]) => boolean;
}
export const StaffContext = createContext<StaffCtx | null>(null);
export function useStaff(): StaffCtx {
  const c = useContext(StaffContext);
  if (!c) throw new Error("useStaff outside StaffContext");
  return c;
}
export const FRONT_OFFICE: Role[] = ["admin", "manager", "receptionist"];
export const MANAGERS: Role[] = ["admin", "manager"];
export const ALL_STAFF: Role[] = ["admin", "manager", "receptionist", "department_staff"];
