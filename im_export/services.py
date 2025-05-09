import logging
from typing import Tuple, Any, Dict, List
from tablib import Dataset

from im_export.resources import InsureeResource
import openpyxl
from decimal import Decimal
from invoice.models import Invoice, PaymentInvoice, DetailPaymentInvoice, InvoiceEvent
from insuree.models import Insuree, Family
from contribution.models import Premium
from policy.models import Policy
from payer.models import Payer
from datetime import datetime
from uuid import uuid4
from django.contrib.contenttypes.models import ContentType
from contribution.services import update_or_create_premium 

logger = logging.getLogger(__name__)


class InsureeImportExportService:
    supported_content_types = {
        'xls': 'application/vnd.ms-excel',
        'csv': 'text/csv',
        'json': 'application/json',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }

    class Strategy:
        INSERT = 'INSERT'
        UPDATE = 'UPDATE'
        INSERT_UPDATE = "INSERT_UPDATE"

    supported_strategies = (Strategy.INSERT, Strategy.UPDATE, Strategy.INSERT_UPDATE)

    def __init__(self, user):
        self._user = user
        self._resource = InsureeResource(user)

    def export_insurees(self, export_format: str = 'csv') -> Tuple[str, Any]:
        if export_format not in self.supported_content_types:
            raise ValueError(f'Non-supported export format: {export_format}')

        # All supported formats match Tablib attrs, to update if that's not valid anymore
        return self.supported_content_types[export_format], \
            getattr(self._resource.export(), export_format)

    def import_insurees(self, import_file, dry_run: bool = False, strategy: str = Strategy.INSERT) \
            -> Tuple[bool, Dict[str, int], List[str]]:

        if not import_file:
            return self._get_general_error('Missing import file')
        if strategy not in self.supported_strategies:
            return self._get_general_error(f'Non-supported strategy: {strategy}')

        # Other strategies are not supported for now
        if strategy in (InsureeImportExportService.Strategy.UPDATE, InsureeImportExportService.Strategy.INSERT_UPDATE):
            strategy = InsureeImportExportService.Strategy.INSERT
            logger.warning(f'Strategy {strategy} not currently supported, defaulting to {InsureeImportExportService.Strategy.INSERT}')

        try:
            if import_file.content_type == 'application/vnd.ms-excel':
                data_set = Dataset(headers=InsureeResource.insuree_headers).load(import_file.open(), 'xls')
            elif import_file.content_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
                data_set = Dataset(headers=InsureeResource.insuree_headers).load(import_file.open(), 'xlsx')
            elif import_file.content_type == 'application/json':
                data_set = Dataset(headers=InsureeResource.insuree_headers).load(import_file.open())
            else:
                data_set = Dataset(headers=InsureeResource.insuree_headers).load(import_file.read().decode())
        except Exception as e:
            return self._get_general_error('Failed to parse input file', e)

        try:
            data_set = self._resource.validate_and_sort_dataset(data_set)
        except Exception as e:
            return self._get_general_error('file validation failed', e)

        dry_run_result = self._resource.import_data(data_set, dry_run=True)  # Test the data import
        totals = self._get_totals_from_result(dry_run_result)
        errors = self._get_errors_from_result(dry_run_result)
        success = not dry_run_result.has_errors() and not dry_run_result.has_validation_errors()

        if not dry_run:
            if success:
                self._resource.import_data(data_set, dry_run=False)  # Actually import
                logger.info(f'Imported {totals["sent"]} insurees')
            else:
                logger.info(f'Failed to import {totals["sent"]} insurees, details: {totals}, errors: {errors}')

        return success, totals, errors

    @staticmethod
    def _get_totals_from_result(result):
        return {
            'sent': result.total_rows,
            'created': result.totals['new'],
            'updated': result.totals['update'],
            'deleted': result.totals['delete'],
            'skipped': result.totals['skip'],
            'invalid': result.totals['invalid'],
            'failed': result.totals['error']
        }
        
    @staticmethod
    def _get_general_error(*args):
        errors = []
        for arg in args:
            errors.append(arg.message if hasattr(arg, 'message') else str(arg))
        totals = {'sent': 0, 'created': 0, 'updated': 0, 'deleted': 0, 'skipped': 0, 'invalid': 0, 'failed': 0}
        success = False

        return success, totals, errors

    @staticmethod
    def _get_errors_from_result(result):
        errors = []
        if result.has_validation_errors():
            for invalid_row in result.invalid_rows:
                errors.append(f"row ({invalid_row.number}) - {invalid_row.error.messages}")
        if result.has_errors():
            for index, row_error in result.row_errors():
                for error in row_error:
                    errors.append(f"row ({index}) - {error.error}")
        return errors

class BankImportService:
    
    def __init__(self, user):
        self._user = user
        self._resource = InsureeResource(user)
    
    # Fonction pour parser les fichier BDC
    def parse_excel_bdc(self, file):
        wb = openpyxl.load_workbook(file)
        sheet = wb.active

        transactions = []
        total_kmf = Decimal("0.00")

        for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
            if not row or all(cell is None for cell in row):
                continue
            if row_idx == 0:
                continue 

            try:
                chf_id = str(int(row[0])) if isinstance(row[0], float) else str(row[0]).strip()
                date = row[2]
                description = str(row[5]).strip()
                amount_raw = row[4]

                if not amount_raw:
                    raise ValueError("Montant manquant ou vide")

                # Nettoyage du montant
                amount_str = str(amount_raw).replace(".", "").replace(",", ".").replace(" ", "")
                amount = Decimal(amount_str)    

                if amount > 0:
                    transactions.append({
                        "insuree_chf_id": chf_id,
                        "date": date.isoformat() if isinstance(date, datetime) else str(date),
                        "description": description,
                        "amount": str(amount),
                        "code_tp": "BDC",
                        "code_ext": f"bdc_{uuid4()}",
                        "code_receipt": f"receipt_{uuid4()}",
                        "label": description,
                        "fees": "0.00",
                        "amount_received": str(amount),
                        "date_payment": date.isoformat() if isinstance(date, datetime) else str(date),
                        "payment_origin": "Banque",
                        "payer_ref": chf_id,
                    })
                    total_kmf += amount
            except Exception as e:
                print(f"Erreur à la ligne {row_idx}: {row} - {e}")
                continue

        return {
            "transactions": transactions,
            "total_kmf": str(total_kmf),
            "count": len(transactions),
        }

    # Fonction pour parser les fichier Exim
    def parse_excel_exim(self, file):
        wb = openpyxl.load_workbook(file)
        sheet = wb.active

        transactions = []
        total_kmf = Decimal("0.00")
        start_parsing = False

        for row in sheet.iter_rows(values_only=True):
            if not start_parsing:
                if row and any("Txn. Date" in str(cell) for cell in row if cell):
                    headers = [str(h).strip() if h else None for h in row]
                    try:
                        date_idx = headers.index("Txn. Date")
                        desc_idx = headers.index("Description")
                        credit_idx = headers.index("Credit")
                        reference_idx = headers.index("Txn.Ref No")
                    except ValueError as e:
                        raise Exception("Colonnes attendues non trouvées dans le fichier (Txn. Date, Description, Credit)") from e
                    start_parsing = True
                    continue

            if start_parsing:
                if row and str(row[1]).startswith("Opening Balance"):
                    break

                try:
                    credit_val = row[credit_idx]
                    if credit_val and Decimal(credit_val) > 0:
                        ref = str(row[reference_idx]) if row[reference_idx] else f"ref_{uuid4()}"
                        date_str = row[date_idx]
                        if isinstance(date_str, datetime):
                            date_formatted = date_str.date().isoformat()
                        else:
                            date_formatted = datetime.strptime(date_str, "%b %d, %Y").date().isoformat()
                        transactions.append({
                            "date": date_formatted,
                            "description": str(row[desc_idx]),
                            "amount": str(Decimal(credit_val)),
                            "insuree_chf_id": ref,
                            "code_ext": ref,
                            "label": str(row[desc_idx]),
                            "code_tp": "EXIM",
                            "code_receipt": f"receipt_{uuid4()}",
                            "fees": "0.00",
                            "amount_received": str(Decimal(credit_val)),
                            "date_payment": date_formatted,
                            "payment_origin": "Banque",
                            "payer_ref": ref,
                        })
                        total_kmf += Decimal(credit_val)
                except Exception as e:
                    print(f"Erreur parsing ligne: {row} - {e}")
                    continue

        return {
            "transactions": transactions,
            "total_kmf": str(total_kmf),
            "count": len(transactions),
        }
    
    def parse_date(self, date_str):
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%b %d, %Y", "%d/%m/%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Format de date non reconnu: {date_str}")


    def log_invoice_event(self, user, invoice, event_type, message):
        event = InvoiceEvent(
            invoice=invoice,
            event_type=event_type,
            message=message,
        )
        event.save(username=user.username)

    def get_invoice_by_code(self, code):
        try:
            return Invoice.objects.get(code=code, is_deleted=False)
        except Invoice.DoesNotExist:
            return None
        
    def find_invoice(self, chf_id):
        try:
            insuree = Insuree.objects.get(chf_id=chf_id, validity_to__isnull=True)
            family = Family.objects.get(head_insuree=insuree, validity_to__isnull=True)
            return Invoice.objects.filter(subject_id=str(family.id), is_deleted=False).order_by("date_invoice").first()
        except (Insuree.DoesNotExist, Family.DoesNotExist):
            return None

    def reconcile_bank_transaction(self, tx):
        chf_id = tx["insuree_chf_id"]
        if not chf_id:
            raise Exception("Insuree CHFID manquant")

        invoice = self.find_invoice(chf_id)
        if not invoice:
            raise Exception(f"Aucune facture trouvée pour le chf_id {chf_id}")

        if invoice.status in [Invoice.Status.PAID, Invoice.Status.CANCELLED]:
            raise Exception(f"Facture déjà payée ou annulée: {invoice.code}")

        payment_date = tx.get("date")
        if not payment_date:
            raise Exception("Date de paiement manquante")
        
        try:
            if isinstance(payment_date, datetime):
                payment_date = payment_date.date()
            elif isinstance(payment_date, str):
                payment_date = self.parse_date(payment_date)
        except ValueError:
            raise Exception(f"Format de date non reconnu: {payment_date}")

        subject_type = ContentType.objects.get_for_model(invoice)

        amount_received = Decimal(tx["amount_received"])
        code_ext = tx.get("code_ext") or f"pay_{uuid4()}"
        code_tp = tx.get("code_tp") or "Banque"
        code_receipt = tx.get("code_receipt") or f"receipt_{uuid4()}"
        label = tx.get("label") or f"Paiement pour {invoice.code}"
        reconciliation_status = PaymentInvoice.ReconciliationStatus.RECONCILIATED
        fees = Decimal(tx.get("fees", "0.00"))
        payment_origin = tx.get("payment_origin") or "Banque"
        payer_ref = tx.get("payer_ref") or chf_id

        payment_invoice = PaymentInvoice(
            code_ext=code_ext,
            code_tp=code_tp,
            code_receipt=code_receipt,
            label=label,
            reconciliation_status=reconciliation_status,
            amount_received=amount_received,
            fees=fees,
            date_payment=payment_date,
            payment_origin=payment_origin,
            payer_ref=payer_ref,
            payer_name=payer_ref
        )
        payment_invoice.save(username=self._user.username)

        detail_payment = DetailPaymentInvoice(
            payment=payment_invoice,
            subject_type=subject_type,
            subject_id=str(invoice.uuid),
            status=DetailPaymentInvoice.DetailPaymentStatus.ACCEPTED,
            fees=fees,
            amount=amount_received,
            reconcilation_id=f"recon_{uuid4()}",
            reconcilation_date=payment_date,
        )
        detail_payment.save(username=self._user.username)

        if invoice.status != Invoice.Status.RECONCILIATED:
            invoice.status = Invoice.Status.RECONCILIATED
            invoice.save(username=self._user.username)
        self.log_invoice_event(
            user=self._user,
            invoice=invoice,
            event_type=InvoiceEvent.EventType.PAYMENT,
            message=f"Paiement reçu pour l'assuré {chf_id}, montant {amount_received} KMF pour la date du {payment_date.strftime('%d/%m/%Y')}"
        )
        
        premium = self.create_premium(chf_id, payment_invoice)
        return {
            "invoice_code": invoice.code,
            "payment_id": payment_invoice.id,
            "detail_payment_id": detail_payment.id,
            "premium_uuid": str(premium.uuid) if premium else None,
            "status": "RECONCILIATED",
            "amount": str(invoice.amount_total),
        }

    def create_premium(self, chf_id, data):
        try:
            insuree = Insuree.objects.get(chf_id=chf_id, validity_to__isnull=True)
            family = Family.objects.get(head_insuree=insuree, validity_to__isnull=True)
            policy = Policy.objects.filter(
                family=family, validity_to__isnull=True, status=Policy.STATUS_IDLE,
            ).order_by("start_date").first()

            if policy:
                payer = Payer.objects.filter(type='C').first()
                
                premium_data = {
                    "audit_user_id": self._user.id,
                    "receipt": data.code_receipt,
                    "pay_date": data.date_payment,
                    "pay_type": "B",
                    "is_photo_fee": False,
                    "amount": data.amount_received,
                    "policy": policy,
                    "payer": payer
                }
                
                premium = Premium(**premium_data)
                created = update_or_create_premium(premium, self._user)
                logger.info(f"Contribution créée avec succès pour police {policy.id}")
                return created
            else:
                logger.warning(f"Aucune police active trouvée pour la famille {family.id}")
        except Exception as e:
            logger.exception(f"Erreur lors de la création de contribution pour CHFID {chf_id}: {e}")
    