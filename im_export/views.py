import json
import logging
from django.http import HttpResponse, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from insuree.apps import InsureeConfig
from .services import InsureeImportExportService
from rest_framework import status
from .serializers import BankImportUploadSerializer
from .models import BankImport
from .utils import parse_bank_file
from core.models import InteractiveUser

logger = logging.getLogger(__name__)


def check_user_rights(rights):
    class UserWithRights(IsAuthenticated):
        def has_permission(self, request, view):
            return super().has_permission(request, view) and request.user.has_perms(rights)

    return UserWithRights


@api_view(["POST"])
@permission_classes([check_user_rights(InsureeConfig.gql_mutation_create_insurees_perms, )])
def import_insurees(request):
    try:
        import_file = request.FILES.get('file', None)
        user = request.user
        dry_run = json.loads(request.POST.get('dry_run', 'false'))
        strategy = request.POST.get('strategy', InsureeImportExportService.Strategy.INSERT)

        success, totals, errors = InsureeImportExportService(user) \
            .import_insurees(import_file, dry_run=dry_run, strategy=strategy)
        return JsonResponse(data={'success': success, 'data': totals, 'errors': errors})
    except ValueError as e:
        logger.error("Error while importing insurees", exc_info=e)
        return Response({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.error("Unexpected error while importing insurees", exc_info=e)
        return Response({'success': False, 'error': str(e)}, status=500)


@api_view(["GET"])
@permission_classes([check_user_rights(InsureeConfig.gql_query_insurees_perms, )])
def export_insurees(request):
    try:
        # TODO add location based filtering
        export_format = request.GET.get("file_format", "csv")
        user = request.user

        content_type, export = InsureeImportExportService(user).export_insurees(export_format)
        response = HttpResponse(export, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="insurees.{export_format}"'
        return response
    except ValueError as e:
        logger.error("Error while exporting insurees", exc_info=e)
        return Response({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.error("Unexpected error while exporting insurees", exc_info=e)
        return Response({'success': False, 'error': str(e)}, status=500)


@api_view(["POST"])
def import_bank_extract(request):
    serializer = BankImportUploadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    file = serializer.validated_data.get("file")
    user = InteractiveUser.objects.filter(login_name=request.user.username).first()
    errors = []
    transactions_result = {}

    try:
        logger.info(f"Upload fichier banque (user={user.id}, file={file})")

        bank_import = BankImport.objects.create(user=user, stored_file=file)

        transactions_result = parse_bank_file(bank_import.stored_file)

        logger.info(f"{transactions_result['count']} transactions créditées extraites")

    except Exception as exc:
        logger.exception("Erreur durant l'import EXIM")
        errors.append(str(exc))

    return Response({
        "success": len(errors) == 0,
        "errors": errors,
        **transactions_result
    }, status=status.HTTP_200_OK if not errors else status.HTTP_400_BAD_REQUEST)

