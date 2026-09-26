/**
 * The box sizes an importer names, as the ISO 6346 size-type codes the API stores — the same codes its
 * import reads from "20GP", "40HC", "1x40HQ" (backend `domain/boxes.py::iso_size_type`). Beware: 45G1
 * is a 40' high cube (the 4 is the length, the 5 the height); a 45-footer is L5G1.
 *
 * The audit compares a charge only with boxes of the same size, so a box without one is never
 * compared: the forms ask for it.
 */
export const CONTAINER_TYPES = ["22G1", "42G1", "45G1", "L5G1"] as const;
export type ContainerType = (typeof CONTAINER_TYPES)[number];

export function isContainerType(value: string | null | undefined): value is ContainerType {
  return (CONTAINER_TYPES as readonly string[]).includes(value ?? "");
}

/**
 * The options of a type select: the four sizes, plus the box's own code when an import or a tracking
 * provider wrote one the list does not name (a reefer, an open top) — shown as written, kept on save.
 */
export function containerTypeOptions(current: string | null | undefined): string[] {
  return current && !isContainerType(current) ? [...CONTAINER_TYPES, current] : [...CONTAINER_TYPES];
}
