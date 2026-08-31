// ============================================================
// Vendor Payments Module
// Card-based UI matching Contractor Payments style.
// Shows vendor summary, financials, payment progress, and actions.
// ============================================================

var vdVendors = [];
var vdMaterials = [];
var vdFilters = { search: '', status: 'all', material: 'all', outstandingOnly: false };
var vdDetailVendor = null;
var vdDetailData = null;

// --- Helpers ---

function vdEscape(s) {
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function vdFmtMoney(amount) {
    return '\u20B9' + (Number(amount) || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

function vdProgressBar(pct) {
    var cls = pct >= 75 ? 'high' : pct >= 40 ? 'mid' : pct > 0 ? 'low' : 'zero';
    return '<div class="progress-bar-container"><div class="progress-bar-fill ' + cls + '" style="width:' + Math.min(pct, 100) + '%;">' + pct + '%</div></div>';
}

function vdStatusBadge(vendor) {
    var status = (vendor.type || '').toLowerCase();
    if (status === 'retired') return '<span class="cp-status cancelled">Retired</span>';
    return '<span class="cp-status active">Active</span>';
}

function vdVentureName(ventureId) {
    if (!ventureId) return '';
    if (typeof venturesList === 'undefined') return '';
    var v = venturesList.find(function(x) { return x.id === ventureId; });
    return v ? v.name : '';
}

// --- Data loading ---

async function vdLoadVendors() {
    try { vdVendors = await apiGet('/api/vendor-directory', { bypassCache: true }) || []; }
    catch (e) { console.error('vdLoadVendors error:', e); vdVendors = []; }
}

async function vdLoadMaterials() {
    try { vdMaterials = await apiGet('/api/inventory-materials') || []; }
    catch (e) { vdMaterials = []; }
}

// --- Panel open/close ---

async function renderVendorDirectoryView() {
    var grid = document.getElementById('vendorCardsGrid');
    if (!grid) return;
    grid.innerHTML = '<div class="att-empty" style="padding:32px 0;text-align:center;color:#999;">Loading...</div>';

    await Promise.all([vdLoadVendors(), vdLoadMaterials()]);
    renderVDMaterialFilter();
    renderVDSummary();
    renderVDCards();
}

function renderVDMaterialFilter() {
    var sel = document.getElementById('vdMaterialFilter');
    if (!sel) return;
    var current = sel.value || 'all';
    var html = '<option value="all">All Materials</option>';
    vdMaterials.forEach(function(m) {
        html += '<option value="' + vdEscape(m.name) + '">' + vdEscape(m.name) + '</option>';
    });
    sel.innerHTML = html;
    if (current !== 'all') sel.value = current;
}

// --- Summary cards ---

function renderVDSummary() {
    var el = document.getElementById('vendorSummaryBar');
    if (!el) return;
    var totalVendors = vdVendors.length;
    var totalPurchased = 0, totalPaid = 0, totalOutstanding = 0;
    vdVendors.forEach(function(v) {
        totalPurchased += parseFloat(v.total_purchased) || 0;
        totalPaid += parseFloat(v.total_paid) || 0;
        totalOutstanding += parseFloat(v.outstanding) || 0;
    });
    el.className = 'cp-summary-bar';
    el.innerHTML =
        '<div class="cp-summary-card"><span class="cp-summary-label">Total Vendors</span><span class="cp-summary-value">' + totalVendors + '</span></div>' +
        '<div class="cp-summary-card"><span class="cp-summary-label">Total Purchased</span><span class="cp-summary-value">' + vdFmtMoney(totalPurchased) + '</span></div>' +
        '<div class="cp-summary-card"><span class="cp-summary-label">Total Paid</span><span class="cp-summary-value po-fin-paid">' + vdFmtMoney(totalPaid) + '</span></div>' +
        '<div class="cp-summary-card"><span class="cp-summary-label">Total Outstanding</span><span class="cp-summary-value po-fin-outstanding">' + vdFmtMoney(totalOutstanding) + '</span></div>';
}

// --- Vendor cards ---

function renderVDCards() {
    var grid = document.getElementById('vendorCardsGrid');
    if (!grid) return;

    var filtered = vdVendors.filter(function(v) {
        if (vdFilters.search) {
            var q = vdFilters.search.toLowerCase();
            var nameMatch = (v.name || '').toLowerCase().indexOf(q) !== -1;
            var matMatch = (v.materials || []).some(function(m) { return m.toLowerCase().indexOf(q) !== -1; });
            var catMatch = (v.categories || []).some(function(c) { return c.toLowerCase().indexOf(q) !== -1; });
            if (!nameMatch && !matMatch && !catMatch) return false;
        }
        if (vdFilters.status !== 'all') {
            var status = (v.type || '').toLowerCase();
            if (vdFilters.status === 'active' && status === 'retired') return false;
            if (vdFilters.status === 'retired' && status !== 'retired') return false;
        }
        if (vdFilters.material && vdFilters.material !== 'all') {
            if (!(v.materials || []).some(function(m) { return m === vdFilters.material; })) return false;
        }
        if (vdFilters.outstandingOnly && (v.outstanding || 0) <= 0) return false;
        return true;
    });

    if (filtered.length === 0) {
        grid.innerHTML = '<div class="att-empty" style="padding:32px 0;text-align:center;">' +
            (vdVendors.length ? 'No vendors match the current filter.' : 'No vendors yet. Click \"+ Add Vendor\" to get started, or vendors are auto-created when you add purchases in Day Book.') +
            '</div>';
        return;
    }

    var html = '';
    filtered.forEach(function(v) {
        var totalPurchased = parseFloat(v.total_purchased) || 0;
        var totalPaid = parseFloat(v.total_paid) || 0;
        var outstanding = parseFloat(v.outstanding) || 0;
        var totalQty = parseFloat(v.total_qty) || 0;
        var unitPrice = parseFloat(v.unit_price) || 0;
        var payPct = totalPurchased > 0 ? Math.round((totalPaid / totalPurchased) * 100) : 0;
        var statusLabel = (v.type || '').toLowerCase() === 'retired' ? 'Retired' : 'Active';
        var statusClass = (v.type || '').toLowerCase() === 'retired' ? 'cancelled' : 'active';
        var outClass = outstanding > 0 ? 'po-fin-outstanding' : 'po-fin-clear';
        var materials = (v.materials || []).join(', ') || '';
        var categories = (v.categories || []).join(', ') || '';
        var ventureName = vdVentureName(v.venture_id || '');
        var subtitleParts = [];
        if (categories) subtitleParts.push(vdEscape(categories));
        if (ventureName) subtitleParts.push(vdEscape(ventureName));
        var subtitle = subtitleParts.length ? subtitleParts.join(' &nbsp;&middot;&nbsp; ') : 'No category';

        html +=
            '<div class="po-card cp-contract-card" data-vid="' + vdEscape(v.id) + '">' +
                '<div class="cp-card-header">' +
                    '<div>' +
                        '<div class="cp-card-title">' + vdEscape(v.name) + '</div>' +
                        '<div class="cp-card-subtitle">' + subtitle + '</div>' +
                    '</div>' +
                    '<div class="cp-card-actions" style="display:flex;align-items:center;gap:6px;">' +
                        '<span class="cp-status ' + statusClass + '">' + statusLabel + '</span>' +
                        (currentUserPermissions && currentUserPermissions.editVendors ? '<button class="cp-delete-contract-btn vd-retire-btn" data-vid="' + vdEscape(v.id) + '" title="Delete vendor">&#128465;</button>' : '') +
                    '</div>' +
                '</div>' +
                '<div class="cp-card-financials">' +
                    '<div class="cp-fin-cell"><span class="cp-fin-label">Total</span><span class="cp-fin-value">' + vdFmtMoney(totalPurchased) + '</span></div>' +
                    '<div class="cp-fin-cell"><span class="cp-fin-label">Paid</span><span class="cp-fin-value po-fin-paid">' + vdFmtMoney(totalPaid) + '</span></div>' +
                    '<div class="cp-fin-cell"><span class="cp-fin-label">Outstanding</span><span class="cp-fin-value ' + outClass + '">' + (outstanding > 0 ? vdFmtMoney(outstanding) : '&#10003; Clear') + '</span></div>' +
                '</div>' +
                '<div class="cp-progress-section">' +
                    '<div class="cp-progress-block">' +
                        '<div class="cp-progress-row"><span class="cp-progress-label">Total Purchased</span><span class="cp-progress-detail">' + vdFmtMoney(totalPurchased) + (totalQty > 0 ? ' &middot; ' + totalQty.toLocaleString('en-IN', { maximumFractionDigits: 2 }) + ' qty' : '') + '</span></div>' +
                    '</div>' +
                    '<div class="cp-progress-block">' +
                        '<div class="cp-progress-row"><span class="cp-progress-label">Payment Progress</span><span class="cp-progress-detail">' + vdFmtMoney(totalPaid) + ' / ' + vdFmtMoney(totalPurchased) + '</span></div>' +
                        vdProgressBar(payPct) +
                    '</div>' +
                '</div>' +
                (materials || unitPrice ? '<div class="cp-card-footer">' +
                    (materials ? '<span>Materials: ' + vdEscape(materials) + '</span>' : '') +
                    (unitPrice ? '<span>Unit Price: ' + vdFmtMoney(unitPrice) + '</span>' : '') +
                '</div>' : '') +
                '<div style="display:flex;gap:8px;padding-top:8px;border-top:1px solid #e8ecf0;">' +
                    '<button class="btn-secondary vd-detail-btn" data-vid="' + vdEscape(v.id) + '" style="flex:1;padding:6px 10px;font-size:0.8rem;">View Details</button>' +
                    '<button class="btn-secondary vd-pay-row-btn" data-vid="' + vdEscape(v.id) + '" style="flex:1;padding:6px 10px;font-size:0.8rem;">+ Payment</button>' +
                    '<button class="btn-secondary vd-edit-btn" data-vid="' + vdEscape(v.id) + '" style="padding:6px 10px;font-size:0.8rem;">Edit</button>' +
                '</div>' +
            '</div>';
    });
    grid.innerHTML = html;

    // Wire card click to open detail (but not when clicking action buttons)
    grid.querySelectorAll('.cp-contract-card[data-vid]').forEach(function(card) {
        card.addEventListener('click', function(e) {
            if (e.target.closest('button')) return;
            openVDDetail(card.dataset.vid);
        });
    });

    // Wire action buttons
    grid.querySelectorAll('.vd-pay-row-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) { e.stopPropagation(); openVDPayment(btn.dataset.vid); });
    });
    grid.querySelectorAll('.vd-detail-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) { e.stopPropagation(); openVDDetail(btn.dataset.vid); });
    });
    grid.querySelectorAll('.vd-edit-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            if (typeof openVendorForm === 'function') openVendorForm(btn.dataset.vid);
        });
    });
    grid.querySelectorAll('.vd-retire-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            vdRetireVendor(btn.dataset.vid);
        });
    });
}

// --- Retire/delete vendor ---

function vdRetireVendor(vendorId) {
    var v = vdVendors.find(function(x) { return x.id === vendorId; });
    if (!v) return;
    showConfirm('Delete Vendor', 'Delete "' + escapeHtml(v.name) + '"? Purchase orders referencing this vendor will still exist.', async function() {
        try {
            await apiDelete('/api/vendor/' + encodeURIComponent(vendorId));
            showToast('Vendor deleted');
            await renderVendorDirectoryView();
        } catch (e) {
            showToast('Failed to delete vendor', true);
        }
    }, null, 'Delete', true);
}

// --- Vendor Detail Modal ---

async function openVDDetail(vendorId) {
    var v = vdVendors.find(function(x) { return x.id === vendorId; });
    if (!v) return;
    vdDetailVendor = v;

    // Hide record payment section for roles without recordPayments permission
    var canRecord = currentUserPermissions && currentUserPermissions.recordPayments;
    var vdModal = document.getElementById('vendorDetailModal');
    if (vdModal) {
        var recordHeaders = vdModal.querySelectorAll('h4');
        recordHeaders.forEach(function(h4) {
            if (h4.textContent.trim() === 'Record Payment') {
                h4.style.display = canRecord ? '' : 'none';
                var next = h4.nextElementSibling;
                if (next && next.classList.contains('invoice-form-row')) next.style.display = canRecord ? '' : 'none';
            }
        });
    }

    document.getElementById('vendorDetailTitle').textContent = v.name;

    var totalPurchased = parseFloat(v.total_purchased) || 0;
    var totalPaid = parseFloat(v.total_paid) || 0;
    var outstanding = parseFloat(v.outstanding) || 0;
    var payPct = totalPurchased > 0 ? Math.round((totalPaid / totalPurchased) * 100) : 0;
    var outClass = outstanding > 0 ? 'po-fin-outstanding' : 'po-fin-clear';

    var summaryEl = document.getElementById('vendorDetailSummary');
    if (summaryEl) {
        summaryEl.innerHTML =
            '<div class="cp-card-financials">' +
                '<div class="cp-fin-cell"><span class="cp-fin-label">Total Purchased</span><span class="cp-fin-value">' + vdFmtMoney(totalPurchased) + '</span></div>' +
                '<div class="cp-fin-cell"><span class="cp-fin-label">Total Paid</span><span class="cp-fin-value po-fin-paid">' + vdFmtMoney(totalPaid) + '</span></div>' +
                '<div class="cp-fin-cell"><span class="cp-fin-label">Outstanding</span><span class="cp-fin-value ' + outClass + '">' + (outstanding > 0 ? vdFmtMoney(outstanding) : '&#10003; Clear') + '</span></div>' +
            '</div>' +
            '<div class="cp-progress-section" style="margin-top:12px;">' +
                '<div class="cp-progress-block">' +
                    '<div class="cp-progress-row"><span class="cp-progress-label">Payment Progress</span><span class="cp-progress-detail">' + vdFmtMoney(totalPaid) + ' / ' + vdFmtMoney(totalPurchased) + '</span></div>' +
                    vdProgressBar(payPct) +
                '</div>' +
            '</div>';
    }

    var infoEl = document.getElementById('vendorDetailInfo');
    if (infoEl) {
        var infoHtml = '';
        if (v.phone || v.gstin || v.type) {
            infoHtml += '<div style="display:flex;gap:16px;margin-bottom:12px;flex-wrap:wrap;">';
            if (v.type) infoHtml += '<div><span style="color:#666;font-size:0.8rem;">Type</span><div>' + vdEscape(v.type) + '</div></div>';
            if (v.phone) infoHtml += '<div><span style="color:#666;font-size:0.8rem;">Phone</span><div>' + vdEscape(v.phone) + '</div></div>';
            if (v.gstin) infoHtml += '<div><span style="color:#666;font-size:0.8rem;">GSTIN</span><div>' + vdEscape(v.gstin) + '</div></div>';
            infoHtml += '</div>';
        }
        if (v.materials && v.materials.length > 0) {
            infoHtml += '<div style="margin-bottom:8px;"><span style="color:#666;font-size:0.8rem;">Materials Supplied:</span> ' +
                v.materials.map(function(m) { return '<span class="vd-mat-chip">' + vdEscape(m) + '</span>'; }).join('') +
            '</div>';
        }
        if (v.categories && v.categories.length > 0) {
            infoHtml += '<div style="margin-bottom:8px;"><span style="color:#666;font-size:0.8rem;">Categories:</span> ' +
                v.categories.map(function(c) { return '<span class="vd-mat-chip">' + vdEscape(c) + '</span>'; }).join('') +
            '</div>';
        }
        infoEl.innerHTML = infoHtml;
    }

    var payDateInput = document.getElementById('vdPayDate');
    if (payDateInput) payDateInput.value = new Date().toISOString().split('T')[0];
    var payAmtInput = document.getElementById('vdPayAmount');
    if (payAmtInput) payAmtInput.value = '';
    var payRefInput = document.getElementById('vdPayRef');
    if (payRefInput) payRefInput.value = '';
    var payNotesInput = document.getElementById('vdPayNotes');
    if (payNotesInput) payNotesInput.value = '';

    var payBtn = document.getElementById('vdRecordPaymentBtn');
    if (payBtn) {
        payBtn.onclick = async function() {
            var amount = parseFloat(document.getElementById('vdPayAmount').value);
            var payment_date = document.getElementById('vdPayDate').value;
            var method = document.getElementById('vdPayMethod').value;
            var reference = document.getElementById('vdPayRef').value.trim();
            var notes = document.getElementById('vdPayNotes').value.trim();
            if (!amount || amount <= 0) { showToast('Please enter a valid amount', true); return; }
            if (!payment_date) { showToast('Please select a payment date', true); return; }
            payBtn.disabled = true;
            payBtn.textContent = 'Saving...';
            try {
                await apiPost('/api/day-book/payment', {
                    vendor_id: vendorId,
                    vendor_name: v.name,
                    amount: amount,
                    payment_date: payment_date,
                    method: method,
                    reference: reference,
                    notes: notes
                });
                showToast('Payment of ' + vdFmtMoney(amount) + ' recorded');
                await renderVendorDirectoryView();
                vdDetailVendor = vdVendors.find(function(x) { return x.id === vendorId; });
                openVDDetail(vendorId);
            } catch (e) {
                showToast('Failed to record payment: ' + (e.message || ''), true);
            } finally {
                payBtn.disabled = false;
                payBtn.textContent = 'Save Payment';
            }
        };
    }

    document.getElementById('vendorDetailModal').classList.add('show');
    await vdLoadDetail(vendorId);
}

function closeVDDetail() {
    document.getElementById('vendorDetailModal').classList.remove('show');
    vdDetailVendor = null;
    vdDetailData = null;
}

async function vdLoadDetail(vendorId) {
    try {
        vdDetailData = await apiGet('/api/day-book/vendor/' + encodeURIComponent(vendorId));
        renderVDPayments();
        renderVDPurchases();
    } catch (e) {
        vdDetailData = { purchases: [], payments: [], summary: {} };
        renderVDPayments();
        renderVDPurchases();
    }
}

function renderVDPayments() {
    var el = document.getElementById('vendorPaymentsList');
    if (!el) return;
    var payments = (vdDetailData && vdDetailData.payments) || [];
    if (!payments.length) {
        el.innerHTML = '<div class="att-empty" style="padding:16px 0;">No payments recorded yet.</div>';
        return;
    }
    var html = '<table class="cp-detail-table"><thead><tr><th class="cp-date-cell">Date</th><th class="cp-amt-cell">Amount</th><th>Method</th><th>Reference</th><th>Notes</th></tr></thead><tbody>';
    payments.forEach(function(p) {
        var methodLabel = (p.method || '').replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
        html += '<tr>' +
            '<td class="cp-date-cell">' + vdEscape(p.payment_date || '\u2014') + '</td>' +
            '<td class="cp-amt-cell po-fin-paid">' + vdFmtMoney(p.amount) + '</td>' +
            '<td>' + vdEscape(methodLabel) + '</td>' +
            '<td>' + vdEscape(p.reference || '\u2014') + '</td>' +
            '<td>' + vdEscape(p.notes || '\u2014') + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

function renderVDPurchases() {
    var el = document.getElementById('vendorPurchasesList');
    if (!el) return;
    var purchases = (vdDetailData && vdDetailData.purchases) || [];
    if (!purchases.length) {
        el.innerHTML = '<div class="att-empty" style="padding:16px 0;">No purchases recorded.</div>';
        return;
    }
    var html = '<table class="cp-detail-table"><thead><tr><th class="cp-date-cell">Invoice Date</th><th>Invoice No</th><th>Material</th><th class="cp-amt-cell">Amount</th></tr></thead><tbody>';
    purchases.forEach(function(p) {
        html += '<tr>' +
            '<td class="cp-date-cell">' + vdEscape(p.invoice_date || '\u2014') + '</td>' +
            '<td>' + vdEscape(p.invoice_no || '\u2014') + '</td>' +
            '<td>' + vdEscape(p.material_name || '\u2014') + '</td>' +
            '<td class="cp-amt-cell">' + vdFmtMoney(p.amount) + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

// --- Quick payment (opens detail modal scrolled to payment section) ---

function openVDPayment(vendorId) {
    openVDDetail(vendorId);
    setTimeout(function() {
        var payInput = document.getElementById('vdPayAmount');
        if (payInput) payInput.focus();
    }, 300);
}

// --- Event wiring ---

(function() {
    var si = document.getElementById('vdSearchInput');
    if (si) {
        si.addEventListener('input', function() {
            vdFilters.search = this.value.trim();
            renderVDCards();
        });
    }
    var sf = document.getElementById('vdStatusFilter');
    if (sf) {
        sf.addEventListener('change', function() {
            vdFilters.status = this.value;
            renderVDCards();
        });
    }
    var mf = document.getElementById('vdMaterialFilter');
    if (mf) {
        mf.addEventListener('change', function() {
            vdFilters.material = this.value;
            renderVDCards();
        });
    }
    var oo = document.getElementById('vdOutstandingOnly');
    if (oo) {
        oo.addEventListener('change', function() {
            vdFilters.outstandingOnly = this.checked;
            renderVDCards();
        });
    }

    var closeDetail = document.getElementById('closeVendorDetail');
    if (closeDetail) closeDetail.addEventListener('click', closeVDDetail);

    var detailModal = document.getElementById('vendorDetailModal');
    if (detailModal) detailModal.addEventListener('click', function(e) {
        if (e.target === detailModal) closeVDDetail();
    });
})();
