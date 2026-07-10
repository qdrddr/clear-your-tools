/** Core SDK metadata. */

import { getVersionNative } from "./native.js";

export function getVersion(): string {
  return getVersionNative();
}
