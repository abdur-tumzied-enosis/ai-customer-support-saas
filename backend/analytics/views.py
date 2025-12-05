from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.db.models import Count, Q, Avg, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta, datetime
import logging

from accounts.models import User, ProjectUser
from channels_app.models import Message, Conversation, ChannelCredential
from knowledge.models import Document, IngestionJob, KnowledgeBase
from usage.models import UsageEvent, UsageCounter

logger = logging.getLogger(__name__)


class AnalyticsOverviewView(APIView):
    """
    API endpoint for analytics overview data
    Supports time range filtering via query parameters
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            # Parse date range from query params
            start_date_str = request.query_params.get('start_date')
            end_date_str = request.query_params.get('end_date')
            
            # Default to last 30 days if not provided
            if start_date_str:
                start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            else:
                start_date = timezone.now() - timedelta(days=30)
            
            if end_date_str:
                end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            else:
                end_date = timezone.now()

            # Get user's projects
            user_project_ids = ProjectUser.objects.filter(user=request.user).values_list('project_id', flat=True)

            # === CONVERSATION METRICS ===
            conversations_qs = Conversation.objects.filter(
                project_id__in=user_project_ids,
                created_at__gte=start_date,
                created_at__lte=end_date
            )
            
            total_conversations = conversations_qs.count()
            
            # Conversations trend by day
            conversations_by_day = list(
                conversations_qs
                .annotate(date=TruncDate('created_at'))
                .values('date')
                .annotate(count=Count('id'))
                .order_by('date')
            )

            # === MESSAGE METRICS ===
            messages_qs = Message.objects.filter(
                project_id__in=user_project_ids,
                created_at__gte=start_date,
                created_at__lte=end_date
            )
            
            total_messages = messages_qs.count()
            inbound_messages = messages_qs.filter(direction='in').count()
            outbound_messages = messages_qs.filter(direction='out').count()
            
            # Messages by status
            messages_by_status = dict(
                messages_qs
                .values('status')
                .annotate(count=Count('id'))
                .values_list('status', 'count')
            )
            
            # Messages trend by day
            messages_by_day = list(
                messages_qs
                .annotate(date=TruncDate('created_at'))
                .values('date')
                .annotate(count=Count('id'))
                .order_by('date')
            )

            # === KNOWLEDGE BASE METRICS ===
            documents_qs = Document.objects.filter(
                kb__project_id__in=user_project_ids,
                created_at__gte=start_date,
                created_at__lte=end_date
            )
            
            total_documents = documents_qs.count()
            
            # Documents by status
            documents_by_status = dict(
                documents_qs
                .values('status')
                .annotate(count=Count('id'))
                .values_list('status', 'count')
            )
            
            # Total storage used (in bytes)
            total_storage = documents_qs.aggregate(total=Sum('file_size'))['total'] or 0
            
            # Ingestion jobs metrics
            ingestion_jobs_qs = IngestionJob.objects.filter(
                kb__project_id__in=user_project_ids,
                created_at__gte=start_date,
                created_at__lte=end_date
            )
            
            total_ingestion_jobs = ingestion_jobs_qs.count()
            ingestion_jobs_by_status = dict(
                ingestion_jobs_qs
                .values('status')
                .annotate(count=Count('id'))
                .values_list('status', 'count')
            )

            # Knowledge bases count
            knowledge_bases_count = KnowledgeBase.objects.filter(
                project_id__in=user_project_ids
            ).count()

            # === CHANNEL METRICS ===
            channels_qs = ChannelCredential.objects.filter(
                project_id__in=user_project_ids
            )
            
            total_channels = channels_qs.count()
            channels_by_type = dict(
                channels_qs
                .values('channel')
                .annotate(count=Count('id'))
                .values_list('channel', 'count')
            )
            
            # Messages by channel (platform)
            messages_by_platform = list(
                Message.objects.filter(
                    project_id__in=user_project_ids,
                    created_at__gte=start_date,
                    created_at__lte=end_date
                )
                .values('contact__platform')
                .annotate(count=Count('id'))
                .order_by('-count')
            )

            # === USER METRICS ===
            # Active users in date range (users who have messages or conversations)
            active_user_ids = set()
            active_user_ids.update(
                Conversation.objects.filter(
                    project_id__in=user_project_ids,
                    created_at__gte=start_date,
                    created_at__lte=end_date
                ).values_list('project__members__user_id', flat=True)
            )
            
            total_active_users = len(active_user_ids)
            
            # New users (sign ups) in date range
            new_users = User.objects.filter(
                date_joined__gte=start_date,
                date_joined__lte=end_date
            ).count()
            
            # Users trend by day
            users_by_day = list(
                User.objects.filter(
                    date_joined__gte=start_date,
                    date_joined__lte=end_date
                )
                .annotate(date=TruncDate('date_joined'))
                .values('date')
                .annotate(count=Count('id'))
                .order_by('date')
            )

            # === TOKEN USAGE METRICS ===
            usage_events_qs = UsageEvent.objects.filter(
                project_id__in=user_project_ids,
                created_at__gte=start_date,
                created_at__lte=end_date
            )
            
            # Total tokens by type
            total_input_tokens = usage_events_qs.filter(kind='chat_in').aggregate(
                total=Sum('tokens')
            )['total'] or 0
            
            total_output_tokens = usage_events_qs.filter(kind='chat_out').aggregate(
                total=Sum('tokens')
            )['total'] or 0
            
            total_embedding_tokens = usage_events_qs.filter(kind='embed').aggregate(
                total=Sum('tokens')
            )['total'] or 0
            
            total_tokens = total_input_tokens + total_output_tokens + total_embedding_tokens
            
            # Token usage trend by day
            tokens_by_day = list(
                usage_events_qs
                .annotate(date=TruncDate('created_at'))
                .values('date')
                .annotate(total_tokens=Sum('tokens'))
                .order_by('date')
            )
            
            # Token usage by kind
            tokens_by_kind = dict(
                usage_events_qs
                .values('kind')
                .annotate(total=Sum('tokens'))
                .values_list('kind', 'total')
            )

            # Prepare response
            analytics_data = {
                "success": True,
                "data": {
                    "date_range": {
                        "start": start_date.isoformat(),
                        "end": end_date.isoformat()
                    },
                    "conversations": {
                        "total": total_conversations,
                        "trend": conversations_by_day
                    },
                    "messages": {
                        "total": total_messages,
                        "inbound": inbound_messages,
                        "outbound": outbound_messages,
                        "by_status": messages_by_status,
                        "by_platform": messages_by_platform,
                        "trend": messages_by_day
                    },
                    "knowledge": {
                        "total_documents": total_documents,
                        "documents_by_status": documents_by_status,
                        "total_storage_bytes": total_storage,
                        "knowledge_bases": knowledge_bases_count,
                        "ingestion_jobs": {
                            "total": total_ingestion_jobs,
                            "by_status": ingestion_jobs_by_status
                        }
                    },
                    "channels": {
                        "total": total_channels,
                        "by_type": channels_by_type
                    },
                    "users": {
                        "active": total_active_users,
                        "new_signups": new_users,
                        "trend": users_by_day
                    },
                    "token_usage": {
                        "total": total_tokens,
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "embedding_tokens": total_embedding_tokens,
                        "by_kind": tokens_by_kind,
                        "trend": tokens_by_day
                    }
                }
            }

            return Response(analytics_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error fetching analytics: {str(e)}", exc_info=True)
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "analytics_fetch_failed",
                        "message": f"Failed to fetch analytics: {str(e)}"
                    }
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

