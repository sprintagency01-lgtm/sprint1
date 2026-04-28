/**
 * Apps Script — Form de onboarding + Sheet vinculado + Doc por cliente.
 *
 * Crea (1) el Google Form, (2) un Sheet de respuestas vinculado, y (3) un
 * trigger que genera un Google Doc formateado por cada respuesta entrante,
 * guardándolo en una carpeta del Drive.
 *
 * ─── INSTALACIÓN (una sola vez) ──────────────────────────────────────────
 *   1. https://script.google.com → Nuevo proyecto.
 *   2. Pega este archivo (sustituye Code.gs).
 *   3. Run → crearFormularioOnboarding()  ← acepta permisos.
 *   4. Run → instalarTrigger()            ← acepta permisos.
 *   5. En "Execution log" verás:
 *        - Edit URL del form (para ti).
 *        - Public URL (la que mandas al cliente).
 *        - Sheet URL (respuestas tabuladas).
 *        - Folder URL (Docs por cliente).
 *
 * ─── QUÉ PASA CADA VEZ QUE UN CLIENTE RESPONDE ──────────────────────────
 *   - Su respuesta aparece como fila nueva en el Sheet.
 *   - Se genera un Doc "Onboarding — {Nombre comercial} — {fecha}" en la
 *     carpeta de Drive.
 *   - El Doc está formateado con secciones, preguntas en negrita y
 *     respuestas debajo.
 *
 * ─── PROPERTIES (para no perder el rastro) ───────────────────────────────
 *   El script guarda en PropertiesService los IDs del form, sheet y carpeta.
 *   Si ya existen, no se duplican: re-ejecutar crearFormularioOnboarding()
 *   crea uno nuevo igualmente — para evitarlo, comprueba antes con verIds().
 */

// ═══════════════════════════════════════════════════════════════════════
//  CREACIÓN DEL FORM + SHEET + CARPETA
// ═══════════════════════════════════════════════════════════════════════

function crearFormularioOnboarding() {
  var form = FormApp.create('Onboarding bot de reservas — Sprintagency');
  form.setDescription(
      'Cinco minutos. En serio. La mayoría de preguntas son de un click.\n\n' +
      'Lo que necesite explicación lo cerramos contigo en una videollamada cortita ' +
      'la semana que viene.')
      .setCollectEmail(true)
      .setProgressBar(true)
      .setShowLinkToRespondAgain(false);

  // ─── PÁGINA 1 — NEGOCIO ─────────────────────────────────────────────
  form.addSectionHeaderItem()
      .setTitle('Tu negocio')
      .setHelpText('4 datos. 30 segundos.');

  form.addTextItem().setTitle('Nombre comercial')
      .setHelpText('El que dirá el bot al descolgar.')
      .setRequired(true);

  form.addTextItem().setTitle('Ciudad').setRequired(true);

  form.addTextItem().setTitle('Teléfono al que redirigir si el bot no puede ayudar')
      .setHelpText('Tiene que contestar alguien.')
      .setRequired(true);

  var sector = form.addMultipleChoiceItem();
  sector.setTitle('Sector')
      .setChoices([
        sector.createChoice('Peluquería / barbería / estética'),
        sector.createChoice('Despacho legal'),
        sector.createChoice('Fisioterapia / clínica'),
        sector.createChoice('Restauración'),
        sector.createChoice('Otro')
      ])
      .setRequired(true);

  // ─── PÁGINA 2 — HORARIO ─────────────────────────────────────────────
  form.addPageBreakItem().setTitle('Horario');

  form.addGridItem()
      .setTitle('Marca tu horario habitual por día')
      .setHelpText('Si tienes turno partido o algo raro, marca "Personalizado" y lo detallamos abajo.')
      .setRows(['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'])
      .setColumns([
        'Cerrado',
        'Mañana (10-14)',
        'Tarde (17-20:30)',
        'Jornada completa (10-20:30)',
        'Turno partido (10-14 y 17-20:30)',
        'Personalizado'
      ])
      .setRequired(true);

  form.addParagraphTextItem()
      .setTitle('Detalles si has marcado "Personalizado" o algo no encaja')
      .setHelpText('P. ej. "los miércoles abrimos a las 9:30, no a las 10". Si todo está bien, déjalo en blanco.')
      .setRequired(false);

  form.addTextItem()
      .setTitle('Vacaciones próximas (si las hay)')
      .setHelpText('Una línea: "del 1 al 15 de agosto". O "ninguna" / déjalo vacío.')
      .setRequired(false);

  // ─── PÁGINA 3 — SERVICIOS ───────────────────────────────────────────
  form.addPageBreakItem().setTitle('Servicios reservables');

  form.addParagraphTextItem()
      .setTitle('¿Qué servicios pueden pedir tus clientes por teléfono?')
      .setHelpText('Una línea por servicio, formato libre pero corto:\n\n' +
                   'Nombre — duración en minutos — precio\n\n' +
                   'Ejemplo:\n' +
                   'Corte hombre — 30 — 15€\n' +
                   'Corte mujer — 45 — 22€\n' +
                   'Coloración — 90 — 55€')
      .setRequired(true);

  // ─── PÁGINA 4 — EQUIPO + CALENDAR ───────────────────────────────────
  form.addPageBreakItem().setTitle('Equipo y agenda');

  var equipo = form.addMultipleChoiceItem();
  equipo.setTitle('¿Cuántas personas atienden?')
      .setChoices([
        equipo.createChoice('Solo yo'),
        equipo.createChoice('2'),
        equipo.createChoice('3'),
        equipo.createChoice('4 o más')
      ])
      .setRequired(true);

  form.addTextItem()
      .setTitle('Nombres del equipo (separados por coma)')
      .setHelpText('Solo el nombre de pila con el que el cliente os pediría cita.\n' +
                   'Ejemplo: "Mario, Marcos, Laura". Si trabajas solo, pon tu nombre.')
      .setRequired(true);

  var cal = form.addMultipleChoiceItem();
  cal.setTitle('¿Usáis Google Calendar para gestionar las citas?')
      .setChoices([
        cal.createChoice('Sí'),
        cal.createChoice('No, usamos otra cosa'),
        cal.createChoice('No usamos agenda digital ahora mismo')
      ])
      .setRequired(true);

  form.addSectionHeaderItem()
      .setTitle('Lo que falte de la agenda lo configuramos juntos')
      .setHelpText('Compartir calendarios, dar permisos, etc. lo hacemos en una videollamada de 15 min. ' +
                   'No tienes que tocar nada hoy.');

  // ─── PÁGINA 5 — PERSONALIZACIÓN ─────────────────────────────────────
  form.addPageBreakItem().setTitle('Cómo quieres que suene el bot');

  form.addTextItem()
      .setTitle('Nombre del asistente')
      .setHelpText('Por defecto: Ana. Si prefieres otro, escríbelo.')
      .setRequired(false);

  var tono = form.addMultipleChoiceItem();
  tono.setTitle('Tono')
      .setChoices([
        tono.createChoice('Cercano (de tú)'),
        tono.createChoice('Profesional (de usted)')
      ])
      .setRequired(true);

  var multi = form.addMultipleChoiceItem();
  multi.setTitle('¿Permites encadenar varios servicios en la misma cita?')
      .setHelpText('Ej.: corte + barba en la misma reserva.')
      .setChoices([
        multi.createChoice('Sí'),
        multi.createChoice('No')
      ])
      .setRequired(true);

  // ─── PÁGINA 6 — TELEFONÍA + CONTACTO ────────────────────────────────
  form.addPageBreakItem().setTitle('Casi acabamos');

  var tel = form.addMultipleChoiceItem();
  tel.setTitle('Número que llamarán los clientes')
      .setChoices([
        tel.createChoice('Quiero que me deis uno nuevo y lo difundo yo'),
        tel.createChoice('Quiero portar mi número actual'),
        tel.createChoice('Aún no lo decido — lo hablamos')
      ])
      .setRequired(true);

  form.addTextItem()
      .setTitle('Persona de contacto (nombre + teléfono o email)')
      .setHelpText('Quien recibe nuestras llamadas/emails para coordinar.')
      .setRequired(true);

  form.addParagraphTextItem()
      .setTitle('¿Algo importante que debamos saber?')
      .setHelpText('Opcional. Manías del negocio, expectativas concretas, o lo que sea.')
      .setRequired(false);

  form.setConfirmationMessage(
      '¡Listo! Te escribimos en 1-2 días para agendar la videollamada de kick-off (15 min) ' +
      'donde cerramos calendarios, número y arrancamos.');

  // ─── SHEET DE RESPUESTAS VINCULADO ──────────────────────────────────
  var sheet = SpreadsheetApp.create('Onboarding bot reservas — respuestas');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());

  // ─── PESTAÑA "Tenants" PARA EL SYNC DESDE EL CMS ─────────────────────
  // El backend (app/sheets_sync.py) escribe aquí cada vez que algo se edita
  // en /admin/clientes/*. Cabecera fija — si la cambias aquí, cámbiala también
  // en HEADERS de sheets_sync.py o el sync sobrescribirá la fila 1 al arrancar.
  var tenantsHeaders = [
    'id', 'name', 'sector', 'status', 'kind', 'plan',
    'phone_display', 'calendar_id', 'timezone',
    'contact_name', 'contact_email',
    'assistant_name', 'assistant_tone', 'assistant_fallback_phone',
    'n_servicios', 'n_equipo',
    'voice_agent_id', 'voice_last_sync_at', 'voice_last_sync_status',
    'created_at', 'updated_at'
  ];
  var tenantsSheet = sheet.insertSheet('Tenants');
  tenantsSheet.getRange(1, 1, 1, tenantsHeaders.length).setValues([tenantsHeaders]);
  tenantsSheet.getRange(1, 1, 1, tenantsHeaders.length).setFontWeight('bold');
  tenantsSheet.setFrozenRows(1);

  // Borrar la "Hoja 1" / "Sheet1" vacía que crea SpreadsheetApp.create() por
  // defecto. Lo hacemos después de añadir Tenants para no quedarnos sin
  // pestañas (Google Sheets no permite borrar la última).
  ['Sheet1', 'Hoja 1', 'Hoja1'].forEach(function(name) {
    var s = sheet.getSheetByName(name);
    if (s && sheet.getSheets().length > 1) {
      try { sheet.deleteSheet(s); } catch (e) { /* tranqui */ }
    }
  });

  // ─── CARPETA DE DRIVE PARA LOS DOCS POR CLIENTE ─────────────────────
  var folder = DriveApp.createFolder('Onboarding clientes — Bot reservas');

  // Agrupamos form + sheet + futuros docs en la misma carpeta para tenerlo ordenado.
  var formFile = DriveApp.getFileById(form.getId());
  var sheetFile = DriveApp.getFileById(sheet.getId());
  formFile.moveTo(folder);
  sheetFile.moveTo(folder);

  // ─── GUARDAR IDS PARA LOS TRIGGERS ──────────────────────────────────
  var props = PropertiesService.getScriptProperties();
  props.setProperties({
    FORM_ID: form.getId(),
    SHEET_ID: sheet.getId(),
    FOLDER_ID: folder.getId()
  });

  Logger.log('═══════════════════════════════════════════════════════════════');
  Logger.log('FORM CREADO');
  Logger.log('Edit URL (para ti):    ' + form.getEditUrl());
  Logger.log('Public URL (cliente):  ' + form.getPublishedUrl());
  Logger.log('Sheet de respuestas:   ' + sheet.getUrl());
  Logger.log('Carpeta de Docs:       ' + folder.getUrl());
  Logger.log('═══════════════════════════════════════════════════════════════');
  Logger.log('PARA EL SYNC CMS → SHEET (Railway):');
  Logger.log('  GOOGLE_SHEETS_ID = ' + sheet.getId());
  Logger.log('  Comparte el Sheet con el email del Service Account (rol Editor).');
  Logger.log('  Más info en SHEETS_SYNC_SETUP.md.');
  Logger.log('═══════════════════════════════════════════════════════════════');
  Logger.log('SIGUIENTE PASO: Run → instalarTrigger()');
  Logger.log('═══════════════════════════════════════════════════════════════');
}


// ═══════════════════════════════════════════════════════════════════════
//  TRIGGER — instala el onFormSubmit una sola vez
// ═══════════════════════════════════════════════════════════════════════

function instalarTrigger() {
  var props = PropertiesService.getScriptProperties();
  var formId = props.getProperty('FORM_ID');
  if (!formId) {
    throw new Error('No hay FORM_ID guardado. Ejecuta primero crearFormularioOnboarding().');
  }

  // Limpiar triggers previos del mismo handler para no duplicar.
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === 'alRecibirRespuesta') {
      ScriptApp.deleteTrigger(t);
    }
  });

  ScriptApp.newTrigger('alRecibirRespuesta')
      .forForm(FormApp.openById(formId))
      .onFormSubmit()
      .create();

  Logger.log('Trigger instalado. A partir de ahora, cada respuesta genera un Doc.');
}


// ═══════════════════════════════════════════════════════════════════════
//  HANDLER — se ejecuta automáticamente al recibir cada respuesta
// ═══════════════════════════════════════════════════════════════════════

function alRecibirRespuesta(e) {
  var props = PropertiesService.getScriptProperties();
  var folderId = props.getProperty('FOLDER_ID');
  if (!folderId) {
    Logger.log('FOLDER_ID no encontrado, abortando.');
    return;
  }
  var folder = DriveApp.getFolderById(folderId);

  var responseObj = e.response;
  var itemResponses = responseObj.getItemResponses();
  var emailResp = '';
  try { emailResp = responseObj.getRespondentEmail() || ''; } catch (err) {}

  // Sacar el nombre comercial para el título del Doc.
  var nombreComercial = '';
  for (var i = 0; i < itemResponses.length; i++) {
    if (itemResponses[i].getItem().getTitle() === 'Nombre comercial') {
      nombreComercial = String(itemResponses[i].getResponse() || '').trim();
      break;
    }
  }
  if (!nombreComercial) nombreComercial = 'Cliente sin nombre';

  var fecha = Utilities.formatDate(new Date(), 'Europe/Madrid', 'yyyy-MM-dd');
  var titulo = 'Onboarding — ' + nombreComercial + ' — ' + fecha;

  // Crear el Doc dentro de la carpeta directamente.
  var doc = DocumentApp.create(titulo);
  var docFile = DriveApp.getFileById(doc.getId());
  docFile.moveTo(folder);

  // Construir el cuerpo del Doc.
  var body = doc.getBody();
  body.clear();

  body.appendParagraph(titulo).setHeading(DocumentApp.ParagraphHeading.TITLE);
  body.appendParagraph('Recibido: ' + Utilities.formatDate(new Date(), 'Europe/Madrid', 'yyyy-MM-dd HH:mm'));
  if (emailResp) body.appendParagraph('Email del cliente: ' + emailResp);
  body.appendHorizontalRule();

  // Estructura por secciones lógicas: agrupamos preguntas conocidas.
  var secciones = [
    { titulo: 'Negocio', preguntas: [
        'Nombre comercial', 'Ciudad',
        'Teléfono al que redirigir si el bot no puede ayudar',
        'Sector'
    ]},
    { titulo: 'Horario', preguntas: [
        'Marca tu horario habitual por día',
        'Detalles si has marcado "Personalizado" o algo no encaja',
        'Vacaciones próximas (si las hay)'
    ]},
    { titulo: 'Servicios', preguntas: [
        '¿Qué servicios pueden pedir tus clientes por teléfono?'
    ]},
    { titulo: 'Equipo y agenda', preguntas: [
        '¿Cuántas personas atienden?',
        'Nombres del equipo (separados por coma)',
        '¿Usáis Google Calendar para gestionar las citas?'
    ]},
    { titulo: 'Personalización del bot', preguntas: [
        'Nombre del asistente', 'Tono',
        '¿Permites encadenar varios servicios en la misma cita?'
    ]},
    { titulo: 'Telefonía y contacto', preguntas: [
        'Número que llamarán los clientes',
        'Persona de contacto (nombre + teléfono o email)',
        '¿Algo importante que debamos saber?'
    ]}
  ];

  // Mapa pregunta → respuesta (para acceso por título).
  var respuestasMap = {};
  itemResponses.forEach(function(ir) {
    var t = ir.getItem().getTitle();
    var r = ir.getResponse();
    respuestasMap[t] = r;
  });

  secciones.forEach(function(sec) {
    body.appendParagraph(sec.titulo).setHeading(DocumentApp.ParagraphHeading.HEADING1);
    sec.preguntas.forEach(function(p) {
      var p_resp = respuestasMap.hasOwnProperty(p) ? respuestasMap[p] : null;
      var preg = body.appendParagraph(p);
      preg.setHeading(DocumentApp.ParagraphHeading.HEADING3);
      if (p_resp === null || p_resp === '' || (Array.isArray(p_resp) && p_resp.length === 0)) {
        body.appendParagraph('— (sin respuesta)').editAsText().setItalic(true);
      } else if (Array.isArray(p_resp)) {
        // Respuesta de matriz (Grid): es un array con una entrada por fila, en orden.
        // Para Grid, el orden de filas es L, M, X, J, V, S, D — lo metemos como tabla.
        if (p === 'Marca tu horario habitual por día') {
          var dias = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];
          var rows = [['Día', 'Horario']];
          for (var k = 0; k < dias.length; k++) {
            rows.push([dias[k], (p_resp[k] || '—')]);
          }
          body.appendTable(rows);
        } else {
          p_resp.forEach(function(v) { body.appendListItem(String(v)); });
        }
      } else {
        body.appendParagraph(String(p_resp));
      }
    });
  });

  // Resumen rápido al final con TODOs internos.
  body.appendHorizontalRule();
  body.appendParagraph('Próximos pasos (interno)').setHeading(DocumentApp.ParagraphHeading.HEADING1);
  body.appendListItem('Revisar respuestas y detectar bloqueantes.');
  body.appendListItem('Agendar videollamada de kick-off (15 min): calendar + número.');
  body.appendListItem('Crear tenant en CMS con id estable.');
  body.appendListItem('Configurar horarios y servicios en CMS según las respuestas.');
  body.appendListItem('Smoke test antes de go-live.');

  doc.saveAndClose();

  Logger.log('Doc creado: ' + doc.getUrl());
}


// ═══════════════════════════════════════════════════════════════════════
//  HELPERS DE DIAGNÓSTICO
// ═══════════════════════════════════════════════════════════════════════

function verIds() {
  var props = PropertiesService.getScriptProperties().getProperties();
  Logger.log(JSON.stringify(props, null, 2));
}

function listarTriggers() {
  ScriptApp.getProjectTriggers().forEach(function(t) {
    Logger.log(t.getHandlerFunction() + ' → ' + t.getEventType());
  });
}

/**
 * Útil si quieres probar el handler sin esperar a que un cliente envíe.
 * Coge la última respuesta del form y regenera su Doc.
 */
function regenerarUltimoDoc() {
  var props = PropertiesService.getScriptProperties();
  var formId = props.getProperty('FORM_ID');
  if (!formId) throw new Error('No hay FORM_ID. Ejecuta crearFormularioOnboarding().');
  var form = FormApp.openById(formId);
  var responses = form.getResponses();
  if (responses.length === 0) {
    Logger.log('Aún no hay respuestas en el form.');
    return;
  }
  alRecibirRespuesta({ response: responses[responses.length - 1] });
}
