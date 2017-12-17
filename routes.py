import os
import datetime
import aiohttp_jinja2
import auth_helpers
from dateutil.relativedelta import relativedelta
import saasu
from aiohttp import web
import ujson
import cbr


@aiohttp_jinja2.template('index.html')
@auth_helpers.login_required
async def index(request, user, saasu_user):
    payroll_from = request.GET.get('from')
    if not payroll_from:
        payroll_from = datetime.date.today()
        payroll_from -= relativedelta(months=int(os.environ.get(
            'DEFAULT_INVOICES_DEPTH', '6')), days=payroll_from.day - 1)
        payroll_from = payroll_from.strftime('%Y-%m-%d')

    invoices = []
    payments_limit = int(os.environ.get('INVOICE_WITH_PAYMENTS', '4'))
    has_payments = False
    async for invoice in saasu.get_invoices(saasu_user['Id'], date_from=payroll_from):
        invoice['payments'] = []
        if payments_limit > 0:
            async for p in saasu.get_payments(invoice['TransactionId'], date_from=invoice['TransactionDate']):
                invoice['payments'].append(p)
                payments_limit -= 1
                has_payments = True
        invoice['payments'] = list(
            sorted(invoice['payments'], key=lambda p: p['CreatedDateUtc']))
        invoice['ApiIdx'] = len(invoices)
        if invoice['payments']:
            invoice['FirstPaymentDate'] = invoice['payments'][0]['CreatedDateUtc']
        else:
            invoice['FirstPaymentDate'] = invoice['TransactionDate']
        invoices.append(invoice)

    invoices = list(sorted(invoices, key=lambda i: (
        i['FirstPaymentDate'][0:10], -i['ApiIdx']), reverse=True))

    return {
        'user': user,
        'saasu_user': saasu_user,
        'payroll_from': payroll_from,
        'invoices': invoices,
        'has_payments': has_payments,
        'payments_limit': os.environ.get('INVOICE_WITH_PAYMENTS', '4'),
        'current_year': int(datetime.datetime.today().strftime('%Y')),
        'current_month': int(datetime.datetime.today().strftime('%-m')),
    }


@aiohttp_jinja2.template('view.html')
@auth_helpers.login_required
async def view_invoice(request, user, saasu_user):
    try:
        invoice = await saasu.get_invoice(request.match_info['invoice_id'])
    except:
        raise web.HTTPNotFound(body='Requested invoice not found')

    if invoice['BillingContactId'] != saasu_user['Id']:
        raise web.HTTPNotFound(body='Requested invoice not found')

    payments = []
    async for payment in saasu.get_payments(invoice['TransactionId']):
        payments.append(payment)

    return {
        'invoice': invoice,
        'payments': payments,
        'sorted_lines': sorted(invoice['LineItems'], key=lambda x: x['TotalAmount'], reverse=True),
    }


@aiohttp_jinja2.template('email.html')
@auth_helpers.login_required
async def email_invoice(request, user, saasu_user):
    try:
        invoice = await saasu.get_invoice(request.match_info['invoice_id'])
    except:
        raise web.HTTPNotFound(body='Requested invoice not found')

    if invoice['BillingContactId'] != saasu_user['Id']:
        raise web.HTTPNotFound(body='Requested invoice not found')

    r = await saasu.email_invoice(invoice['TransactionId'])
    return {
        'invoice': invoice,
        'email_result': r,
        'saasu_user': saasu_user,
    }

@aiohttp_jinja2.template('taxes.html')
@auth_helpers.login_required
async def taxes(request, user, saasu_user):
    year = int(request.match_info['year'])

    invoices = []
    date_from = '%s-12-01' % (year - 1)
    date_to = '%s-12-31' % year
    async for invoice in saasu.get_invoices(saasu_user['Id'], date_from=date_from, date_to=date_to):
        invoices.append(invoice)

    return {
        'year': year,
        'invoices': invoices,
    }


@auth_helpers.login_required
async def invoice_details(request, user, saasu_user):
    try:
        invoice = await saasu.get_invoice(request.match_info['invoice_id'])
    except:
        raise web.HTTPNotFound(body='Requested invoice not found')

    if invoice['BillingContactId'] != saasu_user['Id']:
        raise web.HTTPNotFound(body='Requested invoice not found')

    payments = []
    async for payment in saasu.get_payments(invoice['TransactionId']):
        date = datetime.datetime.strptime(payment['CreatedDateUtc'][0:10], '%Y-%m-%d')
        try:
            payment['cbRate'] = await cbr.get_currency_rate(date, 'USD')
        except:
            payment['cbRate'] = None
        payments.append(payment)

    invoice['payments'] = payments

    return web.json_response(invoice, dumps=ujson.dumps)


@aiohttp_jinja2.template('payment.html')
@auth_helpers.login_required
async def view_payment(request, user, saasu_user):
    try:
        payment = await saasu.get_payment(request.match_info['transaction_id'])
    except:
        raise web.HTTPNotFound(body='Requested payment not found')

    invoice_ids = []
    invoices = {}
    for item in payment.get('PaymentItems', []):
        if item['InvoiceTransactionId'] not in invoice_ids:
            invoice_ids.append(item['InvoiceTransactionId'])
            try:
                invoice = await saasu.get_invoice(item['InvoiceTransactionId'])
                invoices[item['InvoiceTransactionId']] = invoice
            except:
                raise web.HTTPNotFound(body='Requested payment not found')
            if invoice['BillingContactId'] != saasu_user['Id']:
                raise web.HTTPNotFound(body='Requested invoice not found')

    return {
        'invoices': invoices,
        'payment': payment,
    }


def register(app):
    app.router.add_get('/', index)
    app.router.add_get(r'/invoice/{invoice_id:\d+}/email', email_invoice)
    app.router.add_get(r'/invoice/{invoice_id:\d+}/view', view_invoice)
    app.router.add_get(r'/invoice/{invoice_id:\d+}/json', invoice_details)
    app.router.add_get(r'/taxes/{year:\d{4}}', taxes)
    app.router.add_get(r'/payment/{transaction_id:\d+}/view', view_payment)
    app.router.add_static('/', './')
