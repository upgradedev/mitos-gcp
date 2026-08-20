variable "project_id" {
  description = "Google Cloud project. Named upgradegr-<product>, matching the nine that came before it."
  type        = string
  default     = "upgradegr-mitos"
}

variable "region" {
  description = "Cloud Run region."
  type        = string
  default     = "europe-west1"
}

variable "firestore_location" {
  description = "Firestore location. eur3 keeps the provenance thread in Europe, which is the point when the thread is a record of processing."
  type        = string
  default     = "eur3"
}

variable "stage" {
  description = "Deployment stage. A path segment in every secret name, never baked into a value."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "qa", "prod", "uat"], var.stage)
    error_message = "stage must be one of dev, qa, prod, uat."
  }
}

variable "image" {
  description = "Container image for all three services. One image, three identities."
  type        = string
}

variable "model" {
  description = "The Gemini model. The rules require 3.5 or newer, so anything lower is a disqualification rather than a preference."
  type        = string
  default     = "gemini-3.7-flash"

  validation {
    # A single backslash is not a valid HCL escape, so the dot is written as a
    # character class instead. Expressing "3.5 or newer" as a regex over a
    # version string is fiddly enough that the readable form is worth it.
    condition     = can(regex("^gemini-(3[.](5|6|7|8|9)|[4-9][.])", var.model))
    error_message = "the hackathon requires Gemini 3.5 or newer; anything lower is a disqualification, not a preference."
  }
}

variable "spec_repo_remote" {
  description = "SSH remote of the specification repository the writer pushes to."
  type        = string
  default     = "git@github.com:upgradedev/mitos-spec.git"
}

variable "github_owner" {
  description = "Only this owner's repositories may assume the CI identity."
  type        = string
  default     = "upgradedev"
}

variable "github_repo" {
  description = "Only this repository may assume the CI identity."
  type        = string
  default     = "mitos-gcp"
}
