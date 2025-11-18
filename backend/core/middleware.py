from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework import status
from rest_framework.response import Response
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError, APIException
from django.http import JsonResponse
import traceback
import logging
import time

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
	"""
	Middleware to log incoming requests and responses.
	"""
	
	def __init__(self, get_response):
		self.get_response = get_response
	
	def __call__(self, request):
		# Start timer
		start_time = time.time()
		
		# Log request
		user = getattr(request, 'user', None)
		user_info = f"user={user.username}" if user and user.is_authenticated else "user=anonymous"
		
		logger.info(
			f"{request.method} {request.path} - {user_info} - IP: {self._get_client_ip(request)}"
		)
		
		# Process request
		response = self.get_response(request)
		
		# Calculate duration
		duration = time.time() - start_time
		
		# Log response
		logger.info(
			f"{request.method} {request.path} - Status: {response.status_code} - Duration: {duration:.3f}s"
		)
		
		return response
	
	def _get_client_ip(self, request):
		"""Get client IP address from request."""
		x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
		if x_forwarded_for:
			ip = x_forwarded_for.split(',')[0]
		else:
			ip = request.META.get('REMOTE_ADDR')
		return ip


class ExceptionHandlingMiddleware:
	"""
	Middleware to handle exceptions and return consistent error format.
	Replaces DRF's EXCEPTION_HANDLER functionality.
	"""
	
	def __init__(self, get_response):
		self.get_response = get_response
	
	def __call__(self, request):
		response = self.get_response(request)
		return response
	
	def process_exception(self, request, exception):
		"""
		Process exceptions and return formatted JSON responses.
		"""
		# Check if this is an API request (starts with /api/)
		is_api_request = request.path.startswith('/api/')
		
		# Only handle exceptions for API requests
		if not is_api_request:
			return None
		
		# Get view instance for DRF exception handler context
		view_instance = None
		if hasattr(request, 'resolver_match') and request.resolver_match:
			if hasattr(request.resolver_match, 'func'):
				func = request.resolver_match.func
				# Check if it's a class-based view (has 'cls' attribute)
				if hasattr(func, 'cls'):
					view_instance = func.cls
				# Check if it's a function-based view wrapped by DRF
				elif hasattr(func, 'view_class'):
					view_instance = func.view_class
		
		# Handle DRF APIException
		if isinstance(exception, APIException):
			# Log the exception
			logger.warning(
				f"DRF Exception in {request.path}: {type(exception).__name__} - {str(exception)}",
				extra={
					'request_path': request.path,
					'request_method': request.method,
					'exception_type': type(exception).__name__,
					'status_code': getattr(exception, 'status_code', None),
				}
			)
			
			# Use DRF's exception handler for DRF exceptions
			context = {
				'view': view_instance,
				'args': getattr(request.resolver_match, 'args', ()) if request.resolver_match else (),
				'kwargs': getattr(request.resolver_match, 'kwargs', {}) if request.resolver_match else {},
				'request': request,
			}
			
			response = drf_exception_handler(exception, context)
			
			# If DRF couldn't handle it, create a generic response
			if response is None:
				return JsonResponse(
					{
						"success": False,
						"error": {
							"code": "server_error",
							"message": "An unexpected error occurred",
							"details": str(exception) if str(exception) else None,
						},
					},
					status=status.HTTP_500_INTERNAL_SERVER_ERROR,
				)
			
			# Format the response data
			formatted_data = {
				"success": False,
				"error": {
					"code": self._get_error_code(exception),
					"message": self._get_error_message(exception, response),
					"details": self._format_error_details(response.data),
				},
			}
			
			return JsonResponse(formatted_data, status=response.status_code)
		
		# Handle Django validation errors
		if isinstance(exception, DjangoValidationError):
			return JsonResponse(
				{
					"success": False,
					"error": {
						"code": "validation_error",
						"message": "Validation error",
						"details": self._format_error_details(exception.message_dict if hasattr(exception, 'message_dict') else str(exception)),
					},
				},
				status=status.HTTP_400_BAD_REQUEST,
			)
		
		# Handle other exceptions for API requests
		# Log the exception
		logger.error(
			f"Exception in {request.path}: {type(exception).__name__}: {str(exception)}",
			exc_info=True,
			extra={
				'request_path': request.path,
				'request_method': request.method,
				'exception_type': type(exception).__name__,
			}
		)
		
		# Return a generic error response
		return JsonResponse(
			{
				"success": False,
				"error": {
					"code": "server_error",
					"message": "An unexpected error occurred",
					"details": str(exception) if str(exception) else None,
				},
			},
			status=status.HTTP_500_INTERNAL_SERVER_ERROR,
		)
	
	def _get_error_code(self, exc):
		"""Get error code from exception."""
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
	
	def _get_error_message(self, exc, response):
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
	
	def _format_error_details(self, data):
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
			return [self._format_error_details(item) for item in data]
		
		# If it's a dict, format field errors
		if isinstance(data, dict):
			formatted = {}
			for field, errors in data.items():
				if isinstance(errors, list):
					# Take first error message for each field
					formatted[field] = errors[0] if errors else "Invalid value"
				elif isinstance(errors, dict):
					# Nested errors
					formatted[field] = self._format_error_details(errors)
				else:
					formatted[field] = str(errors)
			return formatted
		
		return str(data)

