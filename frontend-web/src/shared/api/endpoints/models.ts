import { get, post } from "@/shared/api/client";
import type { ModelVersion, Page } from "@/shared/types/api";

/** Model versions (spec 04 endpoints 24-25, admin). */
export function listModels(): Promise<Page<ModelVersion>> {
  return get<Page<ModelVersion>>("/models");
}

/** Make this version the only active one. Until hot reload (T5.8) only the loaded one is accepted. */
export function deployModel(version: string): Promise<ModelVersion> {
  return post<ModelVersion>(`/models/${encodeURIComponent(version)}/deploy`);
}
