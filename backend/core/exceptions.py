from rest_framework.views import exception_handler
from rest_framework import status
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
import traceback


def custom_exception_handler(exc, context):
	"""
	Custom exception handler that returns consistent error format.
	"""
	# Call REST framework's default exception handler first
	response = exception_handler(exc, context)
	# log the exception and and it's traceback
	# print(f"Exception: {exc}")
	# print(f"Traceback: {traceback.format_exc()}")

	# If response is None, it means DRF couldn't handle the exception
	# Return a generic error response
	if response is None:
		return Response(
			{
				"success": False,
				"error": {
					"code": "server_error",
					"message": "An unexpected error occurred",
					"details": str(exc) if str(exc) else None,
				},
			},
			status=status.HTTP_500_INTERNAL_SERVER_ERROR,
		)

	# Format the response data
	formatted_data = {
		"success": False,
		"error": {
			"code": get_error_code(exc),
			"message": get_error_message(exc, response),
			"details": format_error_details(response.data),
		},
	}

	return Response(formatted_data, status=response.status_code)


def get_error_code(exc):
	"""Get error code from exception."""
	# Map common exceptions to error codes
	if isinstance(exc, DRFValidationError):
		return "validation_error"
	elif isinstance(exc, DjangoValidationError):
		return "validation_error"
	elif hasattr(exc, "status_code"):
		if exc.status_code == 401:
			return "authentication_error"
		elif exc.status_code == 403:
			return "permission_error"
		elif exc.status_code == 404:
			return "not_found"
		elif exc.status_code == 400:
			return "bad_request"
	
	return "error"


def get_error_message(exc, response):
	"""Get user-friendly error message."""
	# Try to get a message from the exception
	if hasattr(exc, "detail"):
		if isinstance(exc.detail, str):
			return exc.detail
		elif isinstance(exc.detail, list) and len(exc.detail) > 0:
			return exc.detail[0]
		elif isinstance(exc.detail, dict):
			# Get first error message from dict
			first_key = next(iter(exc.detail))
			first_value = exc.detail[first_key]
			if isinstance(first_value, list):
				return first_value[0] if first_value else "Validation error"
			return str(first_value)
	
	# Default messages based on status code
	status_code = response.status_code
	if status_code == 400:
		return "Invalid request data"
	elif status_code == 401:
		return "Authentication required"
	elif status_code == 403:
		return "Permission denied"
	elif status_code == 404:
		return "Resource not found"
	elif status_code == 500:
		return "Internal server error"
	
	return "An error occurred"


def format_error_details(data):
	"""
	Format error details consistently.
	Converts DRF's error format to a consistent structure.
	"""
	if data is None:
		return None
	
	# If it's a string or list of strings, return as is
	if isinstance(data, str):
		return data
	if isinstance(data, list):
		# If all items are strings, return as list
		if all(isinstance(item, str) for item in data):
			return data
		# Otherwise, format each item
		return [format_error_details(item) for item in data]
	
	# If it's a dict, format field errors
	if isinstance(data, dict):
		formatted = {}
		for field, errors in data.items():
			if isinstance(errors, list):
				# Take first error message for each field
				formatted[field] = errors[0] if errors else "Invalid value"
			elif isinstance(errors, dict):
				# Nested errors
				formatted[field] = format_error_details(errors)
			else:
				formatted[field] = str(errors)
		return formatted
	
	return str(data)


