// Direct-to-R2 uploads: the backend hands out a presigned PUT, the browser sends the bytes to R2 itself,
// then tells the backend it is done. File bytes never pass through the API server.
import { ApiError } from "./api";

export type PresignedUpload = { url: string; headers: Record<string, string> };

const MAX_EDGE = 2048;
// Mirror backend/knowledge/models.py (MAX_IMAGES_PER_PRODUCT, MAX_PRODUCT_IMAGE_BYTES); the server stays the authority
export const MAX_PHOTOS_PER_PRODUCT = 10;
export const MAX_PHOTO_BYTES = 10 * 1024 * 1024;

/** Shrink a photo so its long edge is at most maxEdge (JPEG). Speeds up phone uploads; the server still
 * validates and resizes, so on any decode problem the original file is returned unchanged. */
export async function downscaleImage(file: File, maxEdge = MAX_EDGE): Promise<Blob> {
	let bitmap: ImageBitmap;
	try {
		bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
	} catch {
		return file;
	}
	try {
		const scale = maxEdge / Math.max(bitmap.width, bitmap.height);
		if (scale >= 1) return file;
		const canvas = document.createElement("canvas");
		canvas.width = Math.round(bitmap.width * scale);
		canvas.height = Math.round(bitmap.height * scale);
		const ctx = canvas.getContext("2d");
		if (!ctx) return file;
		ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
		const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
		return blob ?? file;
	} finally {
		bitmap.close();
	}
}

export async function sha256Hex(file: Blob): Promise<string> {
	const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
	return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** PUT the bytes to R2 with exactly the headers the presign was signed for. */
export async function putToR2(upload: PresignedUpload, body: Blob): Promise<void> {
	let res: Response;
	try {
		res = await fetch(upload.url, { method: "PUT", headers: upload.headers, body });
	} catch {
		throw new ApiError({ code: "upload_failed", message: "Could not upload the file. Check your connection and try again." });
	}
	if (!res.ok) {
		throw new ApiError({ code: "upload_failed", message: `Upload failed with status ${res.status}` });
	}
}
