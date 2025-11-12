import { ApiError } from "./api";

/**
 * Formats API errors into user-friendly error messages
 * @param err - The error object (ApiError, Error, or unknown)
 * @returns A formatted error message string
 */
export function formatError(err: unknown): string {
	if (err instanceof ApiError) {
		return err.message;
		// Format validation errors from backend
		if (err.details && typeof err.details === "object" && !Array.isArray(err.details)) {
			const fieldErrors = Object.entries(err.details)
				.map(([field, message]) => {
					// Capitalize field name and format message
					const fieldName = field
						.split("_")
						.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
						.join(" ");
					return `${fieldName}: ${message}`;
				})
				.join(", ");
			return fieldErrors || err.message;
		}
		// Handle array of error messages
		if (Array.isArray(err.details)) {
			return err.details.join(", ");
		}
		// Handle string error message
		if (typeof err.details === "string") {
			return err.details;
		}
		return err.message;
	}
	if (err instanceof Error) {
		return err.message;
	}
	return "An unexpected error occurred";
}

