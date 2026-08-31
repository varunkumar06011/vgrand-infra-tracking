// ============================================================
// Day Book Module
// Records all incoming material purchases.
// Fields: Invoice Date, Invoice No, GST/Non-GST, Received Date,
//         Vendor, Material, Category, Type, Qty, Unit, Rate,
//         Total Amount, Remarks
// ============================================================

var ipPurchases = [];
var ipMaterials = [];
var ipCategories = [];
var ipVendors = [];
var ipOutstanding = [];
var ipContractorContracts = [];
var ipFilters = { vendor: 'all', material: 'all', category: 'all', type: 'all', is_gst: 'all', from: '', to: '' };
var ipEditingId = null;

// --- Helpers ---

function ipFmtMoney(amount) {
    return '\u20B9' + (Number(amount) || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

function ipFmtDate(d) {
    if (!d) return '\u2014';
    try { return new Date(d + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }); }
    catch (e) { return d; }
}

function ipParseError(e) {
    var msg = e.message || String(e);
    var m = msg.match(/HTTP \d+: (.+)/);
    if (m) {
        try {
            var j = JSON.parse(m[1]);
            return { error: j.error || m[1], message: j.message || '' };
        } catch (_) {}
        return { error: m[1] };
    }
    return { error: msg };
}

function ipEscape(s) {
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function ipPayTypeLabel(pt) {
    var styles = {
        'vendor': '<span style="color:#666;font-weight:600;">Vendor</span>',
        'contract': '<span style="color:#6366f1;font-weight:600;">Contract</span>',
        'inventory': '<span style="color:#e67e22;font-weight:600;">Inventory</span>',
        'other': '<span style="color:#8e44ad;font-weight:600;">Other</span>'
    };
    return styles[pt] || styles['vendor'];
}

var IP_UNITS = ['kg', 'gram', 'liter', 'ml', 'bag', 'piece', 'box', 'bundle', 'drum', 'roll', 'sheet', 'ton', 'feet', 'sq.ft', 'set', 'lot'];

function ipUnitSelect(selected) {
    var opts = '';
    if (!selected) opts += '<option value="">-- Select Unit --</option>';
    var inList = false;
    IP_UNITS.forEach(function(u) {
        var sel = (u === selected) ? ' selected' : '';
        if (sel) inList = true;
        opts += '<option value="' + u + '"' + sel + '>' + u + '</option>';
    });
    if (selected && !inList) {
        opts += '<option value="' + ipEscape(selected) + '" selected>' + ipEscape(selected) + '</option>';
    }
    return '<select id="ipFormUnit" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;">' + opts + '</select>';
}

// --- Data loading ---

async function ipLoadMaterials() {
    try { ipMaterials = await apiGet('/api/inventory-materials') || []; }
    catch (e) { ipMaterials = []; }
}

async function ipLoadCategories() {
    try {
        var tree = await apiGet('/api/inventory-categories') || [];
        var matCats = await apiGet('/api/materials/categories') || [];
        // Start with tree categories (have types/children)
        var merged = tree.map(function(c) { return c; });
        // Merge in material-only categories that aren't in the tree
        var treeNames = tree.map(function(c) { return (c.name || '').toLowerCase(); });
        matCats.forEach(function(name) {
            if (name && treeNames.indexOf(name.toLowerCase()) === -1) {
                merged.push({ name: name, types: [] });
            }
        });
        merged.sort(function(a, b) {
            return (a.name || '').localeCompare(b.name || '');
        });
        ipCategories = merged;
    } catch (e) { ipCategories = []; }
}

async function ipLoadVendors() {
    try { ipVendors = await apiGet('/api/vendor-directory') || []; }
    catch (e) {
        try { ipVendors = await apiGet('/api/vendors') || []; }
        catch (e2) { ipVendors = []; }
    }
}

async function ipLoadPurchases() {
    var params = new URLSearchParams();
    if (ipFilters.vendor && ipFilters.vendor !== 'all') params.set('vendor_id', ipFilters.vendor);
    if (ipFilters.material && ipFilters.material !== 'all') params.set('material_name', ipFilters.material);
    if (ipFilters.category && ipFilters.category !== 'all') params.set('category', ipFilters.category);
    if (ipFilters.type && ipFilters.type !== 'all') params.set('category_type', ipFilters.type);
    if (ipFilters.is_gst && ipFilters.is_gst !== 'all') params.set('is_gst', ipFilters.is_gst);
    if (ipFilters.from) params.set('from', ipFilters.from);
    if (ipFilters.to) params.set('to', ipFilters.to);
    try { ipPurchases = await apiGet('/api/day-book-entries?' + params.toString()) || []; }
    catch (e) { ipPurchases = []; }
}

async function ipLoadOutstanding() {
    if (!currentUserPermissions || !currentUserPermissions.viewVendorOutstanding) return;
    try { ipOutstanding = await apiGet('/api/day-book/vendor-outstanding') || []; }
    catch (e) { ipOutstanding = []; }
}

async function ipLoadContractorContracts() {
    try { ipContractorContracts = await apiGet('/api/contractor-contracts/for-dropdown') || []; }
    catch (e) { ipContractorContracts = []; }
}

// --- Main render ---

async function renderDayBookView() {
    var content = document.getElementById('dayBookContent');
    if (!content) return;
    content.innerHTML = '<div style="padding:24px;color:#999;">Loading...</div>';

    await Promise.all([ipLoadMaterials(), ipLoadCategories(), ipLoadVendors(), ipLoadPurchases(), ipLoadOutstanding(), ipLoadContractorContracts()]);
    renderIPFilters();
    renderIPSummary();
    renderIPTable();
}

function renderIPFilters() {
    var bar = document.getElementById('dayBookFilters');
    if (!bar) return;

    var vendorOpts = '<option value="all">All Vendors</option>';
    ipVendors.forEach(function(v) {
        vendorOpts += '<option value="' + ipEscape(v.id) + '">' + ipEscape(v.name) + '</option>';
    });

    var materialOpts = '<option value="all">All Materials</option>';
    ipMaterials.forEach(function(m) {
        materialOpts += '<option value="' + ipEscape(m.name) + '">' + ipEscape(m.name) + '</option>';
    });

    var categoryOpts = '<option value="all">All Categories</option>';
    ipCategories.forEach(function(c) {
        categoryOpts += '<option value="' + ipEscape(c.name) + '">' + ipEscape(c.name) + '</option>';
    });

    var typeOpts = '<option value="all">All Types</option>';

    bar.innerHTML =
        '<div class="pending-filter-group"><label>From</label><input type="date" id="ipFilterFrom"></div>' +
        '<div class="pending-filter-group"><label>To</label><input type="date" id="ipFilterTo"></div>' +
        '<div class="pending-filter-group"><label>Vendor</label><select id="ipFilterVendor">' + vendorOpts + '</select></div>' +
        '<div class="pending-filter-group"><label>Material</label><select id="ipFilterMaterial">' + materialOpts + '</select></div>' +
        '<div class="pending-filter-group"><label>Category</label><select id="ipFilterCategory">' + categoryOpts + '</select></div>' +
        '<div class="pending-filter-group"><label>Type</label><select id="ipFilterType">' + typeOpts + '</select></div>' +
        '<div class="pending-filter-group"><label>GST</label><select id="ipFilterGst"><option value="all">All</option><option value="true">GST</option><option value="false">Non-GST</option></select></div>' +
        '<div class="pending-filter-group" style="align-self:flex-end;"><button id="ipFilterApply" class="btn-primary" style="padding:8px 16px;">Apply</button></div>' +
        '<div class="pending-filter-group" style="align-self:flex-end;"><button id="ipFilterClear" class="btn-secondary" style="padding:8px 16px;">Clear</button></div>';

    document.getElementById('ipFilterVendor').value = ipFilters.vendor;
    document.getElementById('ipFilterMaterial').value = ipFilters.material;
    document.getElementById('ipFilterCategory').value = ipFilters.category;
    document.getElementById('ipFilterType').value = ipFilters.type;
    document.getElementById('ipFilterGst').value = ipFilters.is_gst;
    document.getElementById('ipFilterFrom').value = ipFilters.from;
    document.getElementById('ipFilterTo').value = ipFilters.to;

    // Update type dropdown based on selected category
    function updateTypeFilter() {
        var selCat = document.getElementById('ipFilterCategory').value;
        var typeSelect = document.getElementById('ipFilterType');
        var opts = '<option value="all">All Types</option>';
        if (selCat && selCat !== 'all') {
            var cat = ipCategories.find(function(c) { return c.name === selCat; });
            if (cat && cat.types) {
                cat.types.forEach(function(t) {
                    opts += '<option value="' + ipEscape(t.name) + '">' + ipEscape(t.name) + '</option>';
                });
            }
        }
        typeSelect.innerHTML = opts;
        typeSelect.value = ipFilters.type;
    }
    document.getElementById('ipFilterCategory').addEventListener('change', updateTypeFilter);
    updateTypeFilter();

    document.getElementById('ipFilterApply').addEventListener('click', function() {
        ipFilters.vendor = document.getElementById('ipFilterVendor').value;
        ipFilters.material = document.getElementById('ipFilterMaterial').value;
        ipFilters.category = document.getElementById('ipFilterCategory').value;
        ipFilters.type = document.getElementById('ipFilterType').value;
        ipFilters.is_gst = document.getElementById('ipFilterGst').value;
        ipFilters.from = document.getElementById('ipFilterFrom').value;
        ipFilters.to = document.getElementById('ipFilterTo').value;
        renderDayBookView();
    });

    document.getElementById('ipFilterClear').addEventListener('click', function() {
        ipFilters = { vendor: 'all', material: 'all', category: 'all', type: 'all', is_gst: 'all', from: '', to: '' };
        renderDayBookView();
    });
}

function renderIPSummary() {
    var el = document.getElementById('dayBookSummary');
    if (!el) return;
    var totalAmount = ipPurchases.reduce(function(s, p) { return s + (Number(p.amount) || 0); }, 0);
    var html = '<div class="po-fin-row"><span class="po-fin-label">Total Purchases (' + ipPurchases.length + ')</span><span class="po-fin-value">' + ipFmtMoney(totalAmount) + '</span></div>';
    if (currentUserPermissions && currentUserPermissions.viewVendorOutstanding && ipOutstanding.length > 0) {
        var totalOutstanding = ipOutstanding.reduce(function(s, v) { return s + (Number(v.outstanding) || 0); }, 0);
        html += '<div class="po-fin-row"><span class="po-fin-label">Total Vendor Outstanding</span><span class="po-fin-value po-fin-outstanding">' + ipFmtMoney(totalOutstanding) + '</span></div>';
    }
    el.innerHTML = html;
}

function renderIPTable() {
    var content = document.getElementById('dayBookContent');
    if (!content) return;

    if (ipPurchases.length === 0) {
        content.innerHTML = '<div class="att-empty" style="padding:32px 0;text-align:center;color:#999;">No purchases found. Click "+ Add Purchase" to create one.</div>';
        return;
    }

    var html = '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;"><table class="tracker-table" id="dayBookTable"><thead><tr>' +
        '<th>Invoice Date</th><th>Invoice No</th><th>GST</th><th>Received</th>' +
        '<th>Pay To</th><th>Vendor</th><th>Material</th><th>Category</th><th>Type</th>' +
        '<th>Qty</th><th>Unit</th><th>Rate</th><th>Total Amount</th>' +
        '<th>Payment</th><th>Proof</th><th>Remarks</th><th></th>' +
        '</tr></thead><tbody>';

    ipPurchases.forEach(function(p) {
        var paymentLabel = p.payment_method ? p.payment_method.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); }) : '\u2014';
        var proofCell = p.proof_image ? '<button class="btn-text ip-proof-btn" data-id="' + ipEscape(p.id) + '" style="font-size:0.75rem;">View</button>' : '\u2014';
        html += '<tr>' +
            '<td data-label="Invoice Date">' + ipFmtDate(p.invoice_date) + '</td>' +
            '<td data-label="Invoice No">' + ipEscape(p.invoice_no || '\u2014') + '</td>' +
            '<td data-label="GST">' + (p.is_gst ? '<span style="color:#27ae60;">GST</span>' : '<span style="color:#999;">Non-GST</span>') + '</td>' +
            '<td data-label="Received">' + ipFmtDate(p.received_date) + '</td>' +
            '<td data-label="Pay To">' + ipPayTypeLabel(p.payment_type) + '</td>' +
            '<td data-label="Vendor">' + ipEscape(p.vendor_name || '\u2014') + '</td>' +
            '<td data-label="Material">' + ipEscape(p.material_name) + '</td>' +
            '<td data-label="Category">' + ipEscape(p.category || '\u2014') + '</td>' +
            '<td data-label="Type">' + ipEscape(p.category_type || '\u2014') + '</td>' +
            '<td data-label="Qty">' + (Number(p.qty) || 0) + '</td>' +
            '<td data-label="Unit">' + ipEscape(p.unit || '\u2014') + '</td>' +
            '<td data-label="Rate">' + ipFmtMoney(p.rate) + '</td>' +
            '<td data-label="Total Amount">' + ipFmtMoney(p.amount) + '</td>' +
            '<td data-label="Payment">' + ipEscape(paymentLabel) + '</td>' +
            '<td data-label="Proof">' + proofCell + '</td>' +
            '<td data-label="Remarks">' + ipEscape(p.remarks || '\u2014') + '</td>' +
            '<td data-label="Actions"><button class="btn-text ip-edit-btn" data-id="' + ipEscape(p.id) + '" style="font-size:0.75rem;">Edit</button> <button class="btn-text ip-delete-btn" data-id="' + ipEscape(p.id) + '" style="color:#c0392b;font-size:0.75rem;">Delete</button></td>' +
            '</tr>';
    });

    html += '</tbody></table></div>';

    if (currentUserPermissions && currentUserPermissions.viewVendorOutstanding) {
        html += '<div style="margin-top:16px;"><button id="ipVendorOutstandingBtn" class="btn-secondary" style="padding:8px 16px;">View Vendor Outstanding</button></div>';
    }

    content.innerHTML = html;

    content.querySelectorAll('.ip-edit-btn').forEach(function(btn) {
        btn.addEventListener('click', function() { openIPPurchaseForm(btn.dataset.id); });
    });
    content.querySelectorAll('.ip-delete-btn').forEach(function(btn) {
        btn.addEventListener('click', function() { ipDeletePurchase(btn.dataset.id); });
    });
    content.querySelectorAll('.ip-proof-btn').forEach(function(btn) {
        btn.addEventListener('click', function() { viewProofImage(btn.dataset.id); });
    });

    var voBtn = document.getElementById('ipVendorOutstandingBtn');
    if (voBtn) voBtn.addEventListener('click', openVendorOutstandingModal);
}

// --- Add/Edit Purchase form ---

function ipOpenQuickMaterialModal(onCreated) {
    var matModal = document.createElement('div');
    matModal.className = 'modal-overlay';
    matModal.id = 'ipQuickMatModal';
    matModal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:10001;display:flex;align-items:center;justify-content:center;';
    matModal.innerHTML =
        '<div style="background:#fff;border-radius:12px;padding:24px;width:90%;max-width:400px;">' +
            '<h3 style="margin-bottom:16px;">New Inventory Item</h3>' +
            '<div style="margin-bottom:12px;"><label style="font-size:0.85rem;color:#666;">Material Name *</label><input type="text" id="ipQmName" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" placeholder="e.g. Cement, Sand, Wire"></div>' +
            '<div style="margin-bottom:12px;"><label style="font-size:0.85rem;color:#666;">Unit</label>' + ipUnitSelect('') + '</div>' +
            '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">' +
                '<button id="ipQmCancel" class="btn-secondary" style="padding:8px 20px;">Cancel</button>' +
                '<button id="ipQmSave" class="btn-primary" style="padding:8px 20px;">Create</button>' +
            '</div>' +
        '</div>';
    // Fix unit select ID to avoid collision
    var unitSelectEl = matModal.querySelector('#ipFormUnit');
    if (unitSelectEl) unitSelectEl.id = 'ipQmUnit';
    document.body.appendChild(matModal);

    function closeQuickMat() { matModal.remove(); }

    matModal.addEventListener('click', function(e) { if (e.target === matModal) closeQuickMat(); });
    document.getElementById('ipQmCancel').addEventListener('click', closeQuickMat);

    document.getElementById('ipQmSave').addEventListener('click', async function() {
        var name = document.getElementById('ipQmName').value.trim();
        if (!name) { showToast('Material name is required', true); return; }
        var unit = '';
        var unitSel = document.getElementById('ipQmUnit');
        if (unitSel) unit = unitSel.value.trim() || '';

        var saveBtn = document.getElementById('ipQmSave');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Creating...';
        try {
            await apiPost('/api/inventory-material', { name: name, unit: unit || null });
            showToast('Material created');
            closeQuickMat();
            if (onCreated) onCreated(name, unit);
        } catch (e) {
            showToast(ipParseError(e).error || 'Failed to create material', true);
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Create';
        }
    });

    document.getElementById('ipQmName').focus();
}

function openIPPurchaseForm(editId) {
    ipEditingId = editId || null;
    var editing = editId ? ipPurchases.find(function(p) { return p.id === editId; }) : null;

    var materialOpts = ipMaterials.map(function(m) { return '<option value="' + ipEscape(m.name) + '">' + ipEscape(m.name) + '</option>'; }).join('');
    var categoryOpts = ipCategories.map(function(c) { return '<option value="' + ipEscape(c.name) + '">' + ipEscape(c.name) + '</option>'; }).join('');
    var vendorOpts = ipVendors.map(function(v) { return '<option value="' + ipEscape(v.name) + '">' + ipEscape(v.name) + '</option>'; }).join('');
    var contractorOpts = ipContractorContracts.map(function(c) {
        var label = c.person_name + ' — ' + (c.work_description || '').substring(0, 40);
        if (c.status === 'completed') label += ' (Completed)';
        return '<option value="' + ipEscape(c.person_name) + '">' + ipEscape(label) + '</option>';
    }).join('');
    var currentPaymentType = (editing && editing.payment_type) ? editing.payment_type : 'vendor';

    var today = new Date().toISOString().split('T')[0];

    var modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.id = 'ipFormModal';
    modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:10000;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML =
        '<div style="background:#fff;border-radius:12px;padding:24px;width:90%;max-width:600px;max-height:90vh;overflow-y:auto;">' +
            '<h3 style="margin-bottom:16px;">' + (editing ? 'Edit Entry' : 'Add Entry') + '</h3>' +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">' +
                '<div><label style="font-size:0.85rem;color:#666;">Entry Type *</label><select id="ipFormPaymentType" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;"><option value="vendor"' + (currentPaymentType === 'vendor' ? ' selected' : '') + '>Vendor</option><option value="contract"' + (currentPaymentType === 'contract' ? ' selected' : '') + '>Contract Payment</option><option value="inventory"' + (currentPaymentType === 'inventory' ? ' selected' : '') + '>Inventory</option><option value="other"' + (currentPaymentType === 'other' ? ' selected' : '') + '>Other</option></select></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Date *</label><input type="date" id="ipFormInvoiceDate" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + (editing ? (editing.invoice_date || '') : today) + '"></div>' +
            '</div>' +
            '<div id="ipOpenVendorPaymentsLink" style="display:none;margin-top:8px;"><a href="#/vendors" style="font-size:0.85rem;color:#2563eb;cursor:pointer;text-decoration:underline;">Go to Vendor Payments section &rarr;</a></div>' +
            '<div id="ipOpenContractorPaymentsLink" style="display:none;margin-top:8px;"><a href="#/contractor-payments" style="font-size:0.85rem;color:#2563eb;cursor:pointer;text-decoration:underline;">Go to Contractor Payments section &rarr;</a></div>' +
            // --- Vendor / Contract fields ---
            '<div id="ipSectionVendorContract" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;">' +
                '<div id="ipVendorFieldWrap"><label style="font-size:0.85rem;color:#666;">Vendor</label><div style="display:flex;gap:4px;"><input type="text" id="ipFormVendor" list="ipVendorList" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.vendor_name || '') : '') + '" placeholder="Type or select"><datalist id="ipVendorList">' + vendorOpts + '</datalist><button type="button" class="btn-secondary ip-new-vendor-btn" style="padding:8px 10px;font-size:0.8rem;white-space:nowrap;">+ New</button></div></div>' +
                '<div id="ipContractorFieldWrap" style="display:none;"><label style="font-size:0.85rem;color:#666;">Contractor / Contract</label><div style="display:flex;gap:4px;"><input type="text" id="ipFormContract" list="ipContractList" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.vendor_name || '') : '') + '" placeholder="Type or select"><datalist id="ipContractList">' + contractorOpts + '</datalist><button type="button" class="btn-secondary ip-new-contract-btn" style="padding:8px 10px;font-size:0.8rem;white-space:nowrap;">+ New</button></div></div>' +
                '<div></div>' +
            '</div>' +
            // --- Invoice/GST section (hidden for Other) ---
            '<div id="ipSectionInvoiceGst">' +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;">' +
                '<div><label style="font-size:0.85rem;color:#666;">Invoice Number</label><input type="text" id="ipFormInvoiceNo" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.invoice_no || '') : '') + '" placeholder="Optional"></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Received Date *</label><input type="date" id="ipFormReceivedDate" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + (editing ? (editing.received_date || '') : today) + '"></div>' +
            '</div>' +
            '<div class="ip-gst-toggle" style="margin:12px 0;">' +
                '<label style="display:flex;align-items:center;gap:8px;cursor:pointer;"><input type="checkbox" id="ipFormIsGst" ' + (editing && editing.is_gst ? 'checked' : '') + ' style="width:18px;height:18px;"> <span style="font-weight:600;">GST Invoice</span></label>' +
            '</div>' +
            '</div>' +
            // --- Material/Unit section (hidden for Other) ---
            '<div id="ipSectionMaterial">' +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;">' +
                '<div><label style="font-size:0.85rem;color:#666;">Material Name</label><div style="display:flex;gap:4px;"><input type="text" id="ipFormMaterial" list="ipMaterialList" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.material_name || '') : '') + '" placeholder="Type or select"><datalist id="ipMaterialList">' + materialOpts + '</datalist><button type="button" class="btn-secondary ip-new-material-btn" style="padding:8px 10px;font-size:0.8rem;white-space:nowrap;display:none;">+ New</button></div></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Unit</label>' + ipUnitSelect(editing ? (editing.unit || '') : '') + '</div>' +
            '</div>' +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;">' +
                '<div><label style="font-size:0.85rem;color:#666;">Category</label><input type="text" id="ipFormCategory" list="ipCategoryList" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.category || '') : '') + '" placeholder="Optional"><datalist id="ipCategoryList">' + categoryOpts + '</datalist></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Material Type</label><input type="text" id="ipFormType" list="ipTypeList" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.category_type || '') : '') + '" placeholder="Optional - type or select"><datalist id="ipTypeList"></datalist></div>' +
            '</div>' +
            '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:12px;">' +
                '<div><label style="font-size:0.85rem;color:#666;">Quantity</label><input type="number" id="ipFormQty" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + (editing ? (editing.qty || '') : '') + '" step="any"></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Rate</label><input type="number" id="ipFormRate" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + (editing ? (editing.rate || '') : '') + '" step="any"></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Total Amount</label><input type="text" id="ipFormAmount" readonly style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;background:#f4f6f8;"></div>' +
            '</div>' +
            '</div>' +
            // --- Other section (narration + amount + category) ---
            '<div id="ipSectionOther" style="display:none;">' +
            '<div style="margin-top:12px;"><label style="font-size:0.85rem;color:#666;">Narration *</label><input type="text" id="ipFormNarration" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.remarks || '') : '') + '" placeholder="e.g. Water bill, Wi-Fi bill, Electricity"></div>' +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;">' +
                '<div><label style="font-size:0.85rem;color:#666;">Amount *</label><input type="number" id="ipFormOtherAmount" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + (editing ? (editing.amount || '') : '') + '" step="any" min="0"></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Category</label><input type="text" id="ipFormOtherCategory" list="ipOtherCategoryList" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.category || '') : '') + '" placeholder="Optional"><datalist id="ipOtherCategoryList">' + categoryOpts + '</datalist></div>' +
            '</div>' +
            '</div>' +
            // --- Common: Remarks (hidden for Other, uses Narration instead) ---
            '<div id="ipSectionRemarks" style="margin-top:12px;"><label style="font-size:0.85rem;color:#666;">Remarks</label><input type="text" id="ipFormRemarks" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + ipEscape(editing ? (editing.remarks || '') : '') + '" placeholder="Optional"></div>' +
            // Payment Details section
            '<div style="margin-top:16px;padding-top:12px;border-top:1px solid #eee;"><h4 style="margin-bottom:12px;font-size:0.9rem;color:#333;">Payment Details (Optional)</h4>' +
                '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">' +
                    '<div><label style="font-size:0.85rem;color:#666;">Payment Method</label><select id="ipFormPaymentMethod" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;"><option value="">Select Method</option><option value="cash">Cash</option><option value="upi">UPI</option><option value="card">Card</option><option value="bank_transfer">Bank Transfer</option><option value="cheque">Cheque</option><option value="other">Other</option></select></div>' +
                    '<div></div>' +
                '</div>' +
            '</div>' +
            // Proof Upload section
            '<div style="margin-top:12px;"><label style="font-size:0.85rem;color:#666;">Proof / Invoice Image (Optional)</label>' +
                '<div id="ipProofUploadArea" style="border:2px dashed #ccc;border-radius:8px;padding:16px;text-align:center;cursor:pointer;transition:border-color 0.2s;">' +
                    '<div id="ipProofPlaceholder" style="color:#999;font-size:0.85rem;">Click or tap to upload an image<br><span style="font-size:0.75rem;">JPG, PNG, WEBP — max 2MB</span></div>' +
                    '<img id="ipProofPreview" style="display:none;max-width:100%;max-height:200px;border-radius:6px;margin-top:8px;">' +
                    '<input type="file" id="ipProofFile" accept="image/jpeg,image/png,image/webp" style="display:none;">' +
                    '<button id="ipProofRemove" type="button" class="btn-text" style="display:none;color:#c0392b;font-size:0.75rem;margin-top:8px;">Remove Image</button>' +
                '</div>' +
            '</div>' +
            '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px;">' +
                '<button id="ipFormCancel" class="btn-secondary" style="padding:8px 20px;">Cancel</button>' +
                '<button id="ipFormSave" class="btn-primary" style="padding:8px 20px;">Save</button>' +
            '</div>' +
        '</div>';

    document.body.appendChild(modal);

    // Entry Type toggle: Vendor / Contract / Inventory / Other
    var paymentTypeSelect = document.getElementById('ipFormPaymentType');
    var vendorFieldWrap = document.getElementById('ipVendorFieldWrap');
    var contractorFieldWrap = document.getElementById('ipContractorFieldWrap');
    var sectionVendorContract = document.getElementById('ipSectionVendorContract');
    var sectionInvoiceGst = document.getElementById('ipSectionInvoiceGst');
    var sectionMaterial = document.getElementById('ipSectionMaterial');
    var sectionOther = document.getElementById('ipSectionOther');
    var sectionRemarks = document.getElementById('ipSectionRemarks');

    function togglePaymentFields() {
        var pt = paymentTypeSelect.value;
        // Vendor/Contract section visibility
        if (pt === 'vendor' || pt === 'contract') {
            sectionVendorContract.style.display = 'grid';
        } else {
            sectionVendorContract.style.display = 'none';
        }
        // Vendor vs Contract field swap
        if (pt === 'contract') {
            vendorFieldWrap.style.display = 'none';
            contractorFieldWrap.style.display = '';
        } else {
            vendorFieldWrap.style.display = '';
            contractorFieldWrap.style.display = 'none';
        }
        // Invoice/GST section: hidden for Other
        sectionInvoiceGst.style.display = (pt === 'other') ? 'none' : '';
        // Material section: hidden for Other
        sectionMaterial.style.display = (pt === 'other') ? 'none' : '';
        // Material +New button: only for inventory type
        var matNewBtn = modal.querySelector('.ip-new-material-btn');
        if (matNewBtn) matNewBtn.style.display = (pt === 'inventory') ? '' : 'none';
        // Other section: only for Other
        sectionOther.style.display = (pt === 'other') ? '' : 'none';
        // Remarks: hidden for Other (uses Narration instead)
        sectionRemarks.style.display = (pt === 'other') ? 'none' : '';
        // Payment section link: show for vendor/contract entry types
        var vendorLink = document.getElementById('ipOpenVendorPaymentsLink');
        var contractLink = document.getElementById('ipOpenContractorPaymentsLink');
        if (vendorLink) vendorLink.style.display = (pt === 'vendor') ? '' : 'none';
        if (contractLink) contractLink.style.display = (pt === 'contract') ? '' : 'none';
    }
    paymentTypeSelect.addEventListener('change', togglePaymentFields);

    // Set initial state for editing
    if (editing && editing.payment_type === 'contract' && editing.vendor_name) {
        var contractInput = document.getElementById('ipFormContract');
        if (contractInput) contractInput.value = editing.vendor_name;
    }
    togglePaymentFields();

    // --- Payment section navigation links ---
    var vendorPaymentsLink = document.getElementById('ipOpenVendorPaymentsLink');
    if (vendorPaymentsLink) {
        vendorPaymentsLink.addEventListener('click', function(e) {
            e.preventDefault();
            modal.remove();
            if (typeof openVendorDirPanel === 'function') openVendorDirPanel();
        });
    }
    var contractorPaymentsLink = document.getElementById('ipOpenContractorPaymentsLink');
    if (contractorPaymentsLink) {
        contractorPaymentsLink.addEventListener('click', function(e) {
            e.preventDefault();
            modal.remove();
            if (typeof openContractorPaymentsPanel === 'function') openContractorPaymentsPanel();
        });
    }

    // --- + New Vendor button ---
    modal.querySelector('.ip-new-vendor-btn').addEventListener('click', function() {
        if (typeof openVendorForm !== 'function') { showToast('Vendor form not available', true); return; }
        openVendorForm(null);
        // Watch for vendor form modal close, then refresh and auto-select
        var vendorModal = document.getElementById('vendorFormModal');
        if (!vendorModal) return;
        var observer = new MutationObserver(function(mutations) {
            if (!vendorModal.classList.contains('show')) {
                observer.disconnect();
                // Re-fetch vendors from database
                ipLoadVendors().then(function() {
                    // Refresh datalist
                    var dl = document.getElementById('ipVendorList');
                    if (dl) dl.innerHTML = ipVendors.map(function(v) { return '<option value="' + ipEscape(v.name) + '">'; }).join('');
                    // Auto-select the most recently added vendor (last in list, or by name if typed)
                    var vendorInput = document.getElementById('ipFormVendor');
                    if (vendorInput && !vendorInput.value) {
                        var latest = ipVendors[ipVendors.length - 1];
                        if (latest) vendorInput.value = latest.name;
                    }
                    showToast('Vendor list refreshed');
                });
            }
        });
        observer.observe(vendorModal, { attributes: true, attributeFilter: ['class'] });
    });

    // --- + New Contract button ---
    modal.querySelector('.ip-new-contract-btn').addEventListener('click', function() {
        if (typeof openContractForm !== 'function') { showToast('Contract form not available', true); return; }
        openContractForm();
        var contractModal = document.getElementById('contractFormModal');
        if (!contractModal) return;
        var observer = new MutationObserver(function(mutations) {
            if (!contractModal.classList.contains('show')) {
                observer.disconnect();
                ipLoadContractorContracts().then(function() {
                    var dl = document.getElementById('ipContractList');
                    if (dl) dl.innerHTML = ipContractorContracts.map(function(c) {
                        var label = c.person_name + ' \u2014 ' + (c.work_description || '').substring(0, 40);
                        if (c.status === 'completed') label += ' (Completed)';
                        return '<option value="' + ipEscape(c.person_name) + '">';
                    }).join('');
                    var contractInput = document.getElementById('ipFormContract');
                    if (contractInput && !contractInput.value) {
                        var latest = ipContractorContracts[ipContractorContracts.length - 1];
                        if (latest) contractInput.value = latest.person_name;
                    }
                    showToast('Contract list refreshed');
                });
            }
        });
        observer.observe(contractModal, { attributes: true, attributeFilter: ['class'] });
    });

    // --- + New Material button (Inventory type only) ---
    modal.querySelector('.ip-new-material-btn').addEventListener('click', function() {
        ipOpenQuickMaterialModal(function(name, unit) {
            // Refresh material list from database
            ipLoadMaterials().then(function() {
                var dl = document.getElementById('ipMaterialList');
                if (dl) dl.innerHTML = ipMaterials.map(function(m) { return '<option value="' + ipEscape(m.name) + '">'; }).join('');
                var matInput = document.getElementById('ipFormMaterial');
                if (matInput) matInput.value = name;
                if (unit) {
                    var unitSelect = document.getElementById('ipFormUnit');
                    if (unitSelect) unitSelect.value = unit;
                }
            });
        });
    });

    // Amount auto-calc
    var qtyInput = document.getElementById('ipFormQty');
    var rateInput = document.getElementById('ipFormRate');
    var amountInput = document.getElementById('ipFormAmount');
    function updateAmount() {
        var q = parseFloat(qtyInput.value) || 0;
        var r = parseFloat(rateInput.value) || 0;
        amountInput.value = (q * r).toFixed(2);
    }
    qtyInput.addEventListener('input', updateAmount);
    rateInput.addEventListener('input', updateAmount);
    if (editing) updateAmount();

    // Category -> Type datalist
    var categoryInput = document.getElementById('ipFormCategory');
    var typeInput = document.getElementById('ipFormType');
    var typeList = document.getElementById('ipTypeList');
    categoryInput.addEventListener('input', function() {
        var cat = ipCategories.find(function(c) { return c.name.toLowerCase() === categoryInput.value.trim().toLowerCase(); });
        typeList.innerHTML = '';
        if (cat && cat.types) {
            cat.types.forEach(function(t) {
                typeList.innerHTML += '<option value="' + ipEscape(t.name) + '">';
            });
        }
    });
    if (editing && editing.category) {
        categoryInput.dispatchEvent(new Event('input'));
    }

    // Close handlers
    modal.addEventListener('click', function(e) { if (e.target === modal) closeIPPurchaseForm(); });
    document.getElementById('ipFormCancel').addEventListener('click', closeIPPurchaseForm);

    // Proof image upload handlers
    var proofFileInput = document.getElementById('ipProofFile');
    var proofPreview = document.getElementById('ipProofPreview');
    var proofPlaceholder = document.getElementById('ipProofPlaceholder');
    var proofRemoveBtn = document.getElementById('ipProofRemove');
    var proofUploadArea = document.getElementById('ipProofUploadArea');
    var ipProofData = null;

    // Set payment method if editing
    if (editing && editing.payment_method) {
        document.getElementById('ipFormPaymentMethod').value = editing.payment_method;
    }

    // Show existing proof if editing
    if (editing && editing.proof_image) {
        ipProofData = editing.proof_image;
        proofPreview.src = editing.proof_image;
        proofPreview.style.display = 'block';
        proofPlaceholder.style.display = 'none';
        proofRemoveBtn.style.display = 'inline-block';
    }

    proofUploadArea.addEventListener('click', function() { proofFileInput.click(); });
    proofFileInput.addEventListener('change', function(e) {
        var file = e.target.files[0];
        if (!file) return;
        if (file.size > 2 * 1024 * 1024) { showToast('Image must be under 2MB', true); return; }
        var reader = new FileReader();
        reader.onload = function(ev) {
            ipProofData = ev.target.result;
            proofPreview.src = ev.target.result;
            proofPreview.style.display = 'block';
            proofPlaceholder.style.display = 'none';
            proofRemoveBtn.style.display = 'inline-block';
        };
        reader.readAsDataURL(file);
    });
    proofRemoveBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        ipProofData = null;
        proofPreview.src = '';
        proofPreview.style.display = 'none';
        proofPlaceholder.style.display = 'block';
        proofRemoveBtn.style.display = 'none';
        proofFileInput.value = '';
    });

    // Store proof data on the modal element for save handler
    modal._ipProofData = function() { return ipProofData; };

    // Save
    document.getElementById('ipFormSave').addEventListener('click', ipSavePurchase);
}

function closeIPPurchaseForm() {
    var modal = document.getElementById('ipFormModal');
    if (modal) modal.remove();
    ipEditingId = null;
}

async function ipSavePurchase() {
    var paymentType = document.getElementById('ipFormPaymentType').value;
    var invoiceDate = document.getElementById('ipFormInvoiceDate').value;

    if (!invoiceDate) { showToast('Date is required', true); return; }

    var vendorName, vendorId, contractId;
    var materialName = '';
    var qty = 0, rate = 0;
    var isGst = false, invoiceNo = '', receivedDate = '';
    var categoryName = '';

    if (paymentType === 'other') {
        // --- Other: narration + amount + category ---
        var narration = document.getElementById('ipFormNarration').value.trim();
        if (!narration) { showToast('Narration is required', true); return; }
        var otherAmount = parseFloat(document.getElementById('ipFormOtherAmount').value) || 0;
        if (otherAmount <= 0) { showToast('Amount must be greater than 0', true); return; }
        categoryName = document.getElementById('ipFormOtherCategory').value.trim() || null;

        var body = {
            id: ipEditingId || undefined,
            invoice_date: invoiceDate,
            invoice_no: null,
            is_gst: false,
            received_date: invoiceDate,
            vendor_id: null,
            vendor_name: null,
            material_name: narration,
            category: categoryName,
            category_type: null,
            unit: null,
            qty: 1,
            rate: otherAmount,
            amount: otherAmount,
            remarks: narration,
            payment_method: document.getElementById('ipFormPaymentMethod').value || null,
            payment_type: 'other'
        };

        var modalEl = document.getElementById('ipFormModal');
        if (modalEl && modalEl._ipProofData) {
            var proofData = modalEl._ipProofData();
            if (proofData) body.proof_image = proofData;
        }

        var saveBtn = document.getElementById('ipFormSave');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';
        try {
            await apiPost('/api/day-book', body);
            showToast('Entry saved');
            closeIPPurchaseForm();
            await renderDayBookView();
        } catch (e) {
            showToast(ipParseError(e).error || 'Failed to save entry', true);
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save';
        }
        return;
    }

    // --- Vendor / Contract / Inventory: shared fields ---
    isGst = document.getElementById('ipFormIsGst').checked;
    invoiceNo = document.getElementById('ipFormInvoiceNo').value.trim();
    receivedDate = document.getElementById('ipFormReceivedDate').value;
    materialName = document.getElementById('ipFormMaterial').value.trim();

    if (!receivedDate) { showToast('Received Date is required', true); return; }

    qty = parseFloat(document.getElementById('ipFormQty').value) || 0;
    rate = parseFloat(document.getElementById('ipFormRate').value) || 0;
    if (qty <= 0) { showToast('Quantity must be greater than 0', true); return; }

    if (paymentType === 'contract') {
        var contractInputVal = document.getElementById('ipFormContract').value.trim();
        if (!contractInputVal) { showToast('Please enter or select a contractor / contract', true); return; }
        var contract = ipContractorContracts.find(function(c) {
            return c.person_name.toLowerCase() === contractInputVal.toLowerCase();
        });
        if (contract) {
            contractId = contract.id;
            vendorName = contract.person_name;
        } else {
            contractId = null;
            vendorName = contractInputVal;
        }
        vendorId = null;
        if (!materialName) { materialName = 'Contract Payment'; }
    } else if (paymentType === 'vendor') {
        vendorName = document.getElementById('ipFormVendor').value.trim();
        if (!vendorName) { showToast('Vendor is required', true); return; }
        if (!materialName) { showToast('Material Name is required', true); return; }
        var vendor = ipVendors.find(function(v) { return v.name.toLowerCase() === vendorName.toLowerCase(); });
        vendorId = vendor ? vendor.id : null;
    } else if (paymentType === 'inventory') {
        vendorName = null;
        vendorId = null;
        if (!materialName) { showToast('Material Name is required', true); return; }
    }

    categoryName = document.getElementById('ipFormCategory').value.trim() || null;

    var body = {
        id: ipEditingId || undefined,
        invoice_date: invoiceDate,
        invoice_no: invoiceNo || null,
        is_gst: isGst,
        received_date: receivedDate,
        vendor_id: vendorId,
        vendor_name: vendorName,
        material_name: materialName,
        category: categoryName,
        category_type: document.getElementById('ipFormType').value.trim() || null,
        unit: document.getElementById('ipFormUnit').value.trim() || null,
        qty: qty,
        rate: rate,
        remarks: document.getElementById('ipFormRemarks').value.trim() || '',
        payment_method: document.getElementById('ipFormPaymentMethod').value || null,
        payment_type: paymentType
    };
    if (paymentType === 'contract') {
        if (contractId) {
            body.contract_id = contractId;
        } else {
            body.contractor_name = vendorName;
        }
    }

    // Add proof image if uploaded
    var modal = document.getElementById('ipFormModal');
    if (modal && modal._ipProofData) {
        var proofData = modal._ipProofData();
        if (proofData) body.proof_image = proofData;
    }

    // Skip material/category auto-save for contract and other types
    if (paymentType !== 'contract' && paymentType !== 'other') {
    // Auto-save material master if new
    var materialExists = ipMaterials.some(function(m) { return m.name.toLowerCase() === materialName.toLowerCase(); });
    if (!materialExists) {
        try {
            await apiPost('/api/inventory-material', { name: materialName, unit: body.unit });
            await ipLoadMaterials();
        } catch (e) { /* non-fatal */ }
    }

    // Auto-save category if new
    var categoryName = document.getElementById('ipFormCategory').value.trim();
    if (categoryName) {
        var catExists = ipCategories.some(function(c) { return c.name.toLowerCase() === categoryName.toLowerCase() && !c.parent_id; });
        if (!catExists) {
            try {
                await apiPost('/api/inventory-category', { name: categoryName });
                await ipLoadCategories();
            } catch (e) { /* non-fatal */ }
        }
    }

    // Auto-save type if new
    var typeName = document.getElementById('ipFormType').value.trim();
    if (categoryName && typeName) {
        var cat = ipCategories.find(function(c) { return c.name.toLowerCase() === categoryName.toLowerCase() && !c.parent_id; });
        if (cat) {
            var typeExists = cat.types && cat.types.some(function(t) { return t.name.toLowerCase() === typeName.toLowerCase(); });
            if (!typeExists) {
                try {
                    await apiPost('/api/inventory-category', { name: typeName, parent_id: cat.id });
                    await ipLoadCategories();
                } catch (e) { /* non-fatal */ }
            }
        }
    }
    } // end if (paymentType !== 'contract')

    var saveBtn = document.getElementById('ipFormSave');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';

    try {
        await apiPost('/api/day-book', body);
        showToast('Purchase saved');
        closeIPPurchaseForm();
        await renderDayBookView();
    } catch (e) {
        var parsed = ipParseError(e);
        if (parsed.error === 'duplicate') {
            if (confirm(parsed.message || 'A purchase with this invoice number already exists for this vendor. Continue anyway?')) {
                body.force = true;
                try {
                    await apiPost('/api/day-book', body);
                    showToast('Purchase saved');
                    closeIPPurchaseForm();
                    await renderDayBookView();
                } catch (e2) {
                    showToast(ipParseError(e2).error || 'Failed to save purchase', true);
                }
            }
        } else {
            showToast(parsed.error || 'Failed to save purchase', true);
        }
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save';
    }
}

async function ipDeletePurchase(pid) {
    showConfirm('Delete Purchase', 'Delete this purchase entry? This action cannot be undone.', async function() {
        try {
            await apiDelete('/api/day-book/' + encodeURIComponent(pid));
            showToast('Purchase deleted');
            await renderDayBookView();
        } catch (e) {
            showToast(ipParseError(e).error || 'Failed to delete purchase', true);
        }
    }, null, 'Delete', true);
}

// --- Vendor Outstanding Modal (admin) ---

function openVendorOutstandingModal() {
    var modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.id = 'ipVendorModal';
    modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:10000;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML =
        '<div style="background:#fff;border-radius:12px;padding:24px;width:90%;max-width:700px;max-height:90vh;overflow-y:auto;">' +
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">' +
                '<h3>Vendor Outstanding</h3>' +
                '<button id="ipVendorClose" class="btn-secondary" style="padding:6px 16px;">Close</button>' +
            '</div>' +
            '<div id="ipVendorModalBody">Loading...</div>' +
        '</div>';
    document.body.appendChild(modal);

    modal.addEventListener('click', function(e) { if (e.target === modal) modal.remove(); });
    document.getElementById('ipVendorClose').addEventListener('click', function() { modal.remove(); });

    renderVendorOutstandingBody();
}

function renderVendorOutstandingBody() {
    var body = document.getElementById('ipVendorModalBody');
    if (!body) return;

    if (ipOutstanding.length === 0) {
        body.innerHTML = '<div class="att-empty" style="padding:24px;text-align:center;color:#999;">No vendor data available.</div>';
        return;
    }

    var html = '<table class="tracker-table"><thead><tr><th>Vendor</th><th>Total Purchased</th><th>Total Paid</th><th>Outstanding</th><th></th></tr></thead><tbody>';
    ipOutstanding.forEach(function(v) {
        html += '<tr>' +
            '<td>' + ipEscape(v.vendor_name) + '</td>' +
            '<td>' + ipFmtMoney(v.total_purchased) + '</td>' +
            '<td>' + ipFmtMoney(v.total_paid) + '</td>' +
            '<td class="' + (v.outstanding > 0 ? 'po-fin-outstanding' : 'po-fin-clear') + '" style="font-weight:600;">' + ipFmtMoney(v.outstanding) + '</td>' +
            '<td><button class="btn-text ip-vendor-detail-btn" data-vid="' + ipEscape(v.vendor_id) + '" style="font-size:0.75rem;">View Details</button></td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    body.innerHTML = html;

    body.querySelectorAll('.ip-vendor-detail-btn').forEach(function(btn) {
        btn.addEventListener('click', function() { openVendorDetail(btn.dataset.vid); });
    });
}

async function openVendorDetail(vendorId) {
    var body = document.getElementById('ipVendorModalBody');
    if (!body) return;
    body.innerHTML = '<div style="padding:24px;color:#999;">Loading vendor details...</div>';

    try {
        var data = await apiGet('/api/day-book/vendor/' + encodeURIComponent(vendorId));
        var s = data.summary || {};
        var html = '<div style="margin-bottom:16px;">' +
            '<button id="ipVendorBack" class="btn-secondary" style="padding:6px 16px;margin-bottom:12px;">&larr; Back to Outstanding</button>' +
            '<h3 style="margin-bottom:8px;">' + ipEscape((data.purchases[0] || {}).vendor_name || 'Vendor') + '</h3>' +
            '<div style="display:flex;gap:24px;margin-bottom:16px;">' +
                '<div><span style="color:#666;font-size:0.85rem;">Total Purchased</span><div style="font-weight:600;">' + ipFmtMoney(s.total_purchased) + '</div></div>' +
                '<div><span style="color:#666;font-size:0.85rem;">Total Paid</span><div style="font-weight:600;">' + ipFmtMoney(s.total_paid) + '</div></div>' +
                '<div><span style="color:#666;font-size:0.85rem;">Outstanding</span><div style="font-weight:600;color:' + (s.outstanding > 0 ? '#c0392b' : '#27ae60') + ';">' + ipFmtMoney(s.outstanding) + '</div></div>' +
            '</div>' +
        '</div>';

        // Purchase history
        html += '<h4 style="margin-bottom:8px;">Purchase History (' + (data.purchases || []).length + ')</h4>';
        html += '<table class="tracker-table" style="margin-bottom:20px;"><thead><tr><th>Invoice Date</th><th>Invoice No</th><th>Material</th><th>Qty</th><th>Amount</th></tr></thead><tbody>';
        (data.purchases || []).forEach(function(p) {
            html += '<tr><td>' + ipFmtDate(p.invoice_date) + '</td><td>' + ipEscape(p.invoice_no || '\u2014') + '</td><td>' + ipEscape(p.material_name) + '</td><td>' + (Number(p.qty) || 0) + '</td><td>' + ipFmtMoney(p.amount) + '</td></tr>';
        });
        html += '</tbody></table>';

        // Payment history
        html += '<h4 style="margin-bottom:8px;">Payment History (' + (data.payments || []).length + ')</h4>';
        if ((data.payments || []).length > 0) {
            html += '<table class="tracker-table"><thead><tr><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th></tr></thead><tbody>';
            data.payments.forEach(function(p) {
                html += '<tr><td>' + ipFmtDate(p.payment_date) + '</td><td>' + ipFmtMoney(p.amount) + '</td><td>' + ipEscape(p.method) + '</td><td>' + ipEscape(p.reference || '\u2014') + '</td></tr>';
            });
            html += '</tbody></table>';
        } else {
            html += '<div style="color:#999;padding:12px;">No payments recorded.</div>';
        }

        // Record payment button (roles with recordPayments permission)
        if (currentUserPermissions && currentUserPermissions.recordPayments) {
            html += '<div style="margin-top:16px;"><button id="ipRecordPaymentBtn" class="btn-primary" style="padding:8px 20px;">+ Record Payment</button></div>';
        }

        body.innerHTML = html;

        var backBtn = document.getElementById('ipVendorBack');
        if (backBtn) backBtn.addEventListener('click', renderVendorOutstandingBody);

        var payBtn = document.getElementById('ipRecordPaymentBtn');
        if (payBtn) payBtn.addEventListener('click', function() { openIPPaymentForm(vendorId, (data.purchases[0] || {}).vendor_name); });

    } catch (e) {
        body.innerHTML = '<div class="att-empty" style="padding:24px;text-align:center;color:#c0392b;">' + ipEscape(ipParseError(e).error || 'Failed to load vendor details') + '</div>';
    }
}

function openIPPaymentForm(vendorId, vendorName) {
    var today = new Date().toISOString().split('T')[0];
    var modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.id = 'ipPaymentModal';
    modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:10001;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML =
        '<div style="background:#fff;border-radius:12px;padding:24px;width:90%;max-width:450px;">' +
            '<h3 style="margin-bottom:16px;">Record Payment — ' + ipEscape(vendorName || '') + '</h3>' +
            '<div style="display:grid;gap:12px;">' +
                '<div><label style="font-size:0.85rem;color:#666;">Amount</label><input type="number" id="ipPayAmount" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" step="any" placeholder="0.00"></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Payment Date</label><input type="date" id="ipPayDate" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" value="' + today + '"></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Method</label><select id="ipPayMethod" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;"><option value="cash">Cash</option><option value="upi">UPI</option><option value="card">Card</option><option value="cheque">Cheque</option><option value="bank_transfer">Bank Transfer</option><option value="other">Other</option></select></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Reference</label><input type="text" id="ipPayRef" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" placeholder="Optional"></div>' +
                '<div><label style="font-size:0.85rem;color:#666;">Notes</label><input type="text" id="ipPayNotes" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;" placeholder="Optional"></div>' +
            '</div>' +
            '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px;">' +
                '<button id="ipPayCancel" class="btn-secondary" style="padding:8px 20px;">Cancel</button>' +
                '<button id="ipPaySave" class="btn-primary" style="padding:8px 20px;">Record</button>' +
            '</div>' +
        '</div>';
    document.body.appendChild(modal);

    modal.addEventListener('click', function(e) { if (e.target === modal) modal.remove(); });
    document.getElementById('ipPayCancel').addEventListener('click', function() { modal.remove(); });

    document.getElementById('ipPaySave').addEventListener('click', async function() {
        var amount = parseFloat(document.getElementById('ipPayAmount').value) || 0;
        if (amount <= 0) { showToast('Please enter a valid amount', true); return; }
        var paymentDate = document.getElementById('ipPayDate').value;
        if (!paymentDate) { showToast('Please select a payment date', true); return; }

        var saveBtn = document.getElementById('ipPaySave');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Recording...';

        try {
            await apiPost('/api/day-book/payment', {
                vendor_id: vendorId,
                vendor_name: vendorName,
                amount: amount,
                payment_date: paymentDate,
                method: document.getElementById('ipPayMethod').value,
                reference: document.getElementById('ipPayRef').value.trim(),
                notes: document.getElementById('ipPayNotes').value.trim()
            });
            showToast('Payment recorded');
            modal.remove();
            await ipLoadOutstanding();
            openVendorDetail(vendorId);
        } catch (e) {
            showToast(ipParseError(e).error || 'Failed to record payment', true);
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Record';
        }
    });
}

// --- Proof Image Viewer ---

function viewProofImage(purchaseId) {
    var p = ipPurchases.find(function(x) { return x.id === purchaseId; });
    if (!p || !p.proof_image) return;
    var modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:10002;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML =
        '<div style="background:#fff;border-radius:12px;padding:16px;max-width:90%;max-height:90vh;overflow-y:auto;">' +
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">' +
                '<h3 style="font-size:0.9rem;">Proof Image — ' + ipEscape(p.vendor_name || '') + ' / ' + ipEscape(p.material_name || '') + '</h3>' +
                '<button class="btn-secondary" style="padding:4px 12px;font-size:0.8rem;" onclick="this.closest(\'.modal-overlay\').remove();">Close</button>' +
            '</div>' +
            '<img src="' + p.proof_image + '" style="max-width:100%;max-height:70vh;border-radius:8px;">' +
        '</div>';
    modal.addEventListener('click', function(e) { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
}

// --- Wire up Add Purchase button ---
function _wireIPAddBtn() {
    var addBtn = document.getElementById('addDayBookBtn');
    if (addBtn && !addBtn._ipBound) {
        addBtn.addEventListener('click', function() { openIPPurchaseForm(null); });
        addBtn._ipBound = true;
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _wireIPAddBtn);
} else {
    _wireIPAddBtn();
}
